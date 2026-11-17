"""End-to-end check of the intake pipeline.

    docker compose exec web python smoke_intake.py

Builds a scratch customer/project/users, generates a synthetic two-voice recording, pushes
it through the denoise + ASR services, names the speakers, maps them onto project members
and versions the BRD. The LLM step is skipped automatically when the endpoint is
unreachable (a stub version is written instead) so the rest of the chain is still proven.
Everything it creates is deleted at the end.
"""

import io
import json
import os
import struct
import wave

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import math

from django.core.files.base import ContentFile
from django.test import Client

import smoke_fixtures
from apps.intake.models import BRD, TimelineEntry
from apps.intake.services import audio as audio_service
from apps.intake.services import brd as brd_service
from apps.intake.services import llm, pipeline
from apps.projects.models import ProjectMembership

fails = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{(' — ' + str(detail)) if detail else ''}")
    if not ok:
        fails.append(label)


def synth_two_speakers(seconds=8, rate=16000):
    """Two alternating tones plus hiss — enough to exercise decode, denoise and diarization."""
    frames = bytearray()
    for i in range(seconds * rate):
        t = i / rate
        speaker_a = t % 4 < 2
        freq = 140 if speaker_a else 240
        envelope = 0.35 * (1 + math.sin(2 * math.pi * 3 * t)) / 2 + 0.2
        noise = 0.05 * math.sin(2 * math.pi * 5000 * t) * math.cos(t * 31.7)
        sample = envelope * math.sin(2 * math.pi * freq * t) + noise
        frames += struct.pack("<h", int(max(-1, min(1, sample)) * 20000))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))
    return buffer.getvalue()


data = smoke_fixtures.build()
project = data["project"]
author = data["users"]["manager"]
client_user = data["users"]["client"]

try:
    print("[service health]")
    health = audio_service.health()
    check("denoise service up", health.get("denoise", {}).get("ok"), health.get("denoise"))
    check("asr service up", health.get("asr", {}).get("ok"), health.get("asr"))
    llm_health = llm.health()
    llm_up = llm_health.get("ok")
    print(f"  {'ok  ' if llm_up else 'skip'} LLM endpoint {llm.BASE_URL} — {llm_health}")

    print("\n[participants]")
    client_rows = ProjectMembership.objects.filter(project=project, side="client")
    internal_rows = ProjectMembership.objects.filter(project=project, side="internal")
    check("client-side participants exist", client_rows.exists(), client_rows.count())
    check("delivery-side participants exist", internal_rows.count() >= 3, internal_rows.count())
    check(
        "every participant carries role/responsibility",
        all(m.role_description and m.responsibility for m in client_rows),
        ", ".join(m.user.username for m in client_rows),
    )
    check("project belongs to a customer", project.customer_id is not None, project.customer)

    print("\n[ASR endpoint · Qwen, multi-language]")
    import httpx

    from apps.aimodels.runtime import config_for
    from apps.intake.services.audio import ASR_URL

    resolved = config_for("asr", use_cache=False)
    ASR_MODEL = resolved["model"]
    print(f"  resolved ASR: {ASR_MODEL} @ {resolved['base_url']} ({resolved['source']})")
    remote_up = False
    try:
        headers = {"Authorization": f"Bearer {resolved['api_key']}"} if resolved.get("api_key") else {}
        reply = httpx.get(resolved["base_url"] + "/models", headers=headers, timeout=20,
                          **resolved.get("httpx", {}))
        served = [m["id"] for m in reply.json().get("data", [])]
        remote_up = True
    except Exception as exc:  # noqa: BLE001
        served = []
        print(f"  skip remote checks — endpoint unreachable: {type(exc).__name__}: {str(exc)[:90]}")
    if served:
        check("remote ASR serves the configured model", ASR_MODEL in served, served)

    # the service has no CA store of its own: the application pushes the endpoint and the
    # certificate with every request, so a direct probe has to do the same
    from apps.intake.services.audio import asr_settings

    pushed = asr_settings()
    endpoint_fields = {
        "asr_base_url": pushed.get("base_url", ""),
        "asr_api_key": pushed.get("api_key", ""),
        "asr_model": pushed.get("model", ""),
        "ca_cert_path": pushed.get("ca_cert_path", ""),
        "verify_ssl": "0" if pushed.get("verify_ssl") is False else "1",
    }

    probe = httpx.post(
        f"{ASR_URL}/audio/transcriptions",
        files={"file": ("probe.wav", synth_two_speakers(6), "audio/wav")},
        data={"response_format": "verbose_json", "diarize": "true", "backend": "remote", **endpoint_fields},
        timeout=300,
    ).json()
    check("diarization split the audio into turns", probe.get("turns", 0) >= 2, probe.get("turns"))
    check("diarization is pyannote or the mock stand-in",
          probe.get("diarization_engine") in ("pyannote", "mock"), probe.get("diarization_model"))
    check("language passthrough accepted", httpx.post(
        f"{ASR_URL}/audio/transcriptions",
        files={"file": ("probe.wav", synth_two_speakers(4), "audio/wav")},
        data={"response_format": "verbose_json", "language": "de", "backend": "remote", **endpoint_fields},
        timeout=300,
    ).json().get("language") == "de")
    if remote_up:
        check("each turn went to the remote ASR model", probe.get("asr_engine") == "qwen-remote",
              probe.get("asr_engine"))
        check("silence yields no invented text", probe.get("text") == "", repr(probe.get("text"))[:40])
        health = httpx.get(ASR_URL.replace("/v1", "") + "/health", timeout=20).json()
        check("remote endpoint reachable after the call", health["asr"]["reachable"] is True, health["asr"])
    else:
        print("  skip 3 remote-ASR checks — the configured endpoint is not reachable from here")

    # the synthetic tones carry no speech, so pin the mock backend for the pipeline leg
    os.environ["ASR_BACKEND_REQUEST"] = "mock"

    print("\n[audio pipeline]")
    entry = TimelineEntry.objects.create(
        project=project, title="Smoke: client call", kind="audio", author=author,
        body="", status="queued", include_in_brd=True,
    )
    entry.upload.save("smoke-call.wav", ContentFile(synth_two_speakers()), save=True)
    entry.original_name = "smoke-call.wav"
    entry.save()

    pipeline.process_entry(entry, regenerate_brd=False)
    entry.refresh_from_db()
    media = entry.media_set.first()
    check("entry processed", entry.status in ("ready", "awaiting_speakers"), entry.status)
    check("media asset created", media is not None)
    check("denoised rendition stored", bool(media and media.denoised), media.denoise_engine if media else "")
    check("transcript segments stored", entry.segments.count() > 0, f"{entry.segments.count()} segments")
    speakers = list(entry.speakers.all())
    check("speakers detected", len(speakers) >= 1, [s.label for s in speakers])
    check("speakers start unnamed", all(not s.is_named for s in speakers))
    check("entry waits for names", entry.status == "awaiting_speakers", entry.status)
    if media:
        print(f"       denoise engine={media.denoise_engine} asr engine={media.asr_engine} "
              f"duration={media.duration_seconds:.1f}s")

    print("\n[speaker naming + user mapping]")
    web = Client()
    web.force_login(author)
    payload = {}
    for index, speaker in enumerate(speakers):
        prefix = f"sp{speaker.pk}"
        if index == 0:
            payload[f"{prefix}-user"] = str(client_user.pk)
            payload[f"{prefix}-side"] = "client"
        else:
            payload[f"{prefix}-display_name"] = "Lee Lead"
            payload[f"{prefix}-side"] = "internal"
    payload["update_brd"] = "0"
    response = web.post(f"/entries/{entry.pk}/speakers/", payload)
    check("speaker form accepted", response.status_code == 302, response.status_code)
    entry.refresh_from_db()
    speakers = list(entry.speakers.all())
    check("all speakers named", all(s.is_named for s in speakers), [s.resolved_name for s in speakers])
    check("first speaker mapped to a real user", speakers[0].user_id == client_user.pk)
    check("status advanced to ready", entry.status == "ready", entry.status)
    check("transcript renders names, not labels", "SPEAKER_" not in entry.content_for_llm(),
          entry.content_for_llm()[:90])

    print("\n[BRD versioning]")
    BRD.objects.filter(project=project).delete()
    if llm_up:
        version = brd_service.regenerate(project, entries=[entry], user=author)
        check("v1 generated by the LLM", version.number == 1, version.model_name)
        check("markdown returned", len(version.markdown) > 200, f"{len(version.markdown)} chars")
        check("speaker name present in the BRD", client_user.first_name in version.markdown
              or client_user.last_name in version.markdown)
        second = brd_service.regenerate(project, entries=[], user=author, note="second pass")
        brd = BRD.objects.get(project=project)
        check("v2 created immutably",
              second.number == 2 and version.markdown == brd.versions.get(number=1).markdown)
        # an unchanged rewrite legitimately produces an empty diff; only assert the pairing
        check("diff matches the content change",
              bool(second.diff) == (second.markdown != version.markdown), second.stats)
        third = brd.add_version(version.markdown + "\n\nFR-999 appended by the smoke test.",
                                summary="diff probe", created_by=author, model_name="manual")
        check("diff captures an edit", "+FR-999 appended by the smoke test." in third.diff, third.stats)
    else:
        brd = BRD.objects.create(project=project, title="stub")
        v1 = brd.add_version("# Stub v1\n\nFR-001 initial.", summary="stub", created_by=author, model_name="stub")
        v2 = brd.add_version("# Stub v1\n\nFR-001 initial.\nFR-002 added.", summary="stub 2",
                             created_by=author, model_name="stub")
        check("versions increment", (v1.number, v2.number) == (1, 2))
        check("older version unchanged", brd.versions.get(number=1).markdown == "# Stub v1\n\nFR-001 initial.")
        check("diff recorded", "+FR-002 added." in v2.diff, v2.stats)
        check("prompt builds without the LLM",
              client_user.first_name in brd_service.build_messages(project, "", [entry])[1]["content"])

    print("\n[notifications]")
    from apps.notify import services as notifications
    from apps.notify import vapid
    from apps.notify.models import Notification, PushSubscription

    check("VAPID key available", len(vapid.public_key()) > 60, len(vapid.public_key()))
    check("processing notified the project", Notification.objects.filter(
        user=author, kind__in=["entry_processed", "speakers_needed"]).exists())
    if llm_up:
        check("BRD update notified the project", Notification.objects.filter(
            user=author, kind="brd_updated").exists())
    else:
        print("  skip the BRD notification check — the LLM endpoint is not reachable from here")
    note = notifications.notify(author, title="smoke", body="b", url="/", kind="test")
    check("notification stored without a subscription", note is not None and not note.pushed)
    fake = PushSubscription.objects.create(
        user=author, endpoint="https://fcm.googleapis.com/fcm/send/smoke-invalid",
        p256dh="BJ" + "A" * 85, auth="A" * 22, user_agent="smoke",
    )
    delivered, error = notifications.push_to_user(author, note)
    check("a dead subscription fails softly", delivered == 0 and bool(error), error[:60])
    PushSubscription.objects.filter(pk=fake.pk).delete()
    check("service worker is served from the root", web.get("/sw.js").status_code == 200)
    check("public key endpoint", web.get("/api/push/public-key/").json().get("enabled"))
    sub = web.post("/api/push/subscribe/", json.dumps({
        "endpoint": "https://example.com/smoke-endpoint",
        "keys": {"p256dh": "BJ" + "A" * 85, "auth": "A" * 22},
    }), content_type="application/json")
    check("subscribe endpoint", sub.status_code == 200 and sub.json().get("ok"), sub.status_code)
    check("subscription stored", PushSubscription.objects.filter(
        user=author, endpoint="https://example.com/smoke-endpoint").exists())
    unsub = web.post("/api/push/unsubscribe/", json.dumps(
        {"endpoint": "https://example.com/smoke-endpoint"}), content_type="application/json")
    check("unsubscribe endpoint", unsub.json().get("deleted") == 1, unsub.json())

    print("\n[speaker audio samples]")
    page = web.get(f"/entries/{entry.pk}/").content.decode()
    check("listen buttons rendered", page.count("play-speaker") >= 2, page.count("play-speaker"))
    check("per-line play buttons rendered", page.count("seg-play") >= 1, page.count("seg-play"))
    clip = web.get(f"/entries/{entry.pk}/clip/?ranges=0.00-2.00,3.00-4.00")
    check("clip endpoint returns wav", clip.status_code == 200 and clip["Content-Type"] == "audio/wav",
          clip.status_code)
    body = b"".join(clip.streaming_content) if clip.streaming else clip.content
    # 3 seconds of 16 kHz mono 16-bit = 96044 bytes with the header
    check("clip is exactly the requested ranges", 90000 < len(body) < 100000, len(body))
    check("clip rejects an over-long request",
          web.get(f"/entries/{entry.pk}/clip/?ranges=0-5000").status_code == 400)
    check("clip needs a range", web.get(f"/entries/{entry.pk}/clip/").status_code == 404)
    anon = Client()
    check("clip is login-gated", anon.get(f"/entries/{entry.pk}/clip/?ranges=0-2").status_code == 302)
    check("media is login-gated", anon.get("/media/anything.wav").status_code == 302)

    print("\n[transcript editing + sync]")
    entry.refresh_from_db()
    check("named speakers count as named", entry.unnamed_speakers == 0, entry.unnamed_speakers)
    first = entry.segments.first()
    before = entry.segments.count()
    resp = web.post(f"/entries/{entry.pk}/transcript/", {
        f"seg{first.pk}-speaker_label": first.speaker_label,
        f"seg{first.pk}-start": first.start,
        f"seg{first.pk}-end": first.end,
        f"seg{first.pk}-text": "Corrected by the smoke test.",
        "new-speaker": first.speaker_label, "new-start": 90, "new-end": 92,
        "new-text": "An appended line.",
    })
    check("transcript editor accepts a POST", resp.status_code == 302, resp.status_code)
    first.refresh_from_db()
    entry.refresh_from_db()
    check("line text was corrected", first.text == "Corrected by the smoke test.", first.text[:40])
    check("line was appended", entry.segments.count() == before + 1, entry.segments.count())
    check("entry marked for re-sync", entry.brd_applied is False)
    check("plain transcript rebuilt", "Corrected by the smoke test." in entry.transcript)
    if llm_up:
        sync = web.post(f"/entries/{entry.pk}/sync/")
        entry.refresh_from_db()
        check("sync button creates a version", sync.status_code == 302, sync.status_code)
        check("sync marked the entry applied", entry.brd_applied is True)
    check("transcript editor page", web.get(f"/entries/{entry.pk}/transcript/").status_code == 200)
    check("notifications inbox", web.get("/notifications/").status_code == 200)
    check("unread feed", web.get("/api/notifications/unread/").status_code == 200)

    print("\n[pages]")
    for label, url in [
        ("timeline", f"/projects/{project.pk}/timeline/"),
        ("entry detail", f"/entries/{entry.pk}/"),
        ("participants", f"/projects/{project.pk}/participants/"),
        ("customer detail", f"/customers/{project.customer_id}/"),
        ("brd", f"/projects/{project.pk}/brd/"),
        ("brd history", f"/projects/{project.pk}/brd/history/"),
        ("brd download", f"/projects/{project.pk}/brd/download/"),
        ("entry status api", f"/api/entries/{entry.pk}/status/"),
    ]:
        response = web.get(url)
        check(f"GET {label}", response.status_code == 200, response.status_code)

    guest = Client()
    guest.force_login(data["users"]["guest"])
    check("guest cannot regenerate the BRD",
          guest.post(f"/projects/{project.pk}/brd/regenerate/").status_code == 403)
    member = Client()
    member.force_login(data["users"]["member"])
    check("member cannot regenerate the BRD",
          member.post(f"/projects/{project.pk}/brd/regenerate/").status_code == 403)

finally:
    smoke_fixtures.teardown()

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILURES:\n" + "\n".join(fails)))
raise SystemExit(1 if fails else 0)
