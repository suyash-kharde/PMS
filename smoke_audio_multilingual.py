"""Real-audio check of the intake audio pipeline — Marathi / Hindi / English and code-mix.

    docker compose exec web python smoke_audio_multilingual.py                # reprocess the newest audio entry
    docker compose exec web python smoke_audio_multilingual.py --entry 17
    docker compose exec web python smoke_audio_multilingual.py --file /app/media/call.m4a --project 5
    docker compose exec web python smoke_audio_multilingual.py --entry 17 --brd

No mocks anywhere: the run fails if the ASR text came from the placeholder engine, if the
diarizer fell back to even slices, or if the denoiser used the numpy fallback. Use
--allow-fallback to downgrade those three to warnings when the real weights are absent.

Beyond the plumbing it reports what actually came back per speaker turn: which script the
text is in (Devanagari / Latin / mixed), Marathi-vs-Hindi marker hits, and how much of the
recording is code-mixed — the thing that matters for these recordings.
"""

import argparse
import os
import re
import unicodedata

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import httpx
from django.core.files.base import ContentFile

from apps.aimodels.runtime import config_for
from apps.intake.models import TimelineEntry
from apps.intake.services import audio as audio_service
from apps.intake.services import pipeline
from apps.projects.models import Project

parser = argparse.ArgumentParser()
parser.add_argument("--entry", type=int, help="reprocess an existing timeline entry")
parser.add_argument("--file", help="path inside the container to a fresh recording")
parser.add_argument("--project", type=int, help="project id for --file")
parser.add_argument("--language", default="", help="force a language on the pipeline leg (default: auto)")
parser.add_argument("--brd", action="store_true", help="also regenerate the BRD (needs every speaker named)")
parser.add_argument("--allow-fallback", action="store_true", help="mock/fallback engines warn instead of fail")
parser.add_argument("--matrix-seconds", type=float, default=20.0, help="slice length for the language matrix")
parser.add_argument("--matrix-offset", type=float, default=60.0, help="where the first matrix slice starts")
parser.add_argument("--matrix-slices", type=int, default=3, help="how many slices to spread across the recording")
args = parser.parse_args()

fails = []
warns = []

# marker words that separate the two Devanagari languages; scored, not decisive on their own
MARATHI_MARKERS = ["आहे", "आहेत", "नाही", "नाहीये", "मला", "तुम्ही", "आम्ही", "करतो", "करते", "झाला",
                   "झालं", "काय", "कशाला", "पाहिजे", "थोडं", "आणि", "किती", "इथे", "तिथे", "बघ",
                   "टाक", "सांग", "होय", "बरं", "मग"]
HINDI_MARKERS = ["है", "हैं", "नहीं", "मुझे", "आप", "हम", "करता", "करती", "हुआ", "क्या", "चाहिए",
                 "थोड़ा", "और", "कितना", "यहाँ", "वहाँ", "देख", "डाल", "बता", "हाँ", "अच्छा", "तो",
                 "कर", "गया"]
ENGLISH_HINT = re.compile(r"[A-Za-z]{2,}")


def check(label, ok, detail="", soft=False):
    if ok:
        print(f"  ok   {label}{(' — ' + str(detail)) if detail else ''}")
        return True
    if soft:
        print(f"  warn {label}{(' — ' + str(detail)) if detail else ''}")
        warns.append(label)
    else:
        print(f"  FAIL {label}{(' — ' + str(detail)) if detail else ''}")
        fails.append(label)
    return False


def scripts_of(text: str):
    """-> (devanagari_letters, latin_letters)"""
    dev = lat = 0
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("DEVANAGARI"):
            dev += 1
        elif name.startswith("LATIN"):
            lat += 1
    return dev, lat


def classify_script(text: str) -> str:
    dev, lat = scripts_of(text)
    if dev and lat:
        return "mixed"
    if dev:
        return "devanagari"
    if lat:
        return "latin"
    return "none"


def marker_hits(text: str):
    marathi = sum(text.count(word) for word in MARATHI_MARKERS)
    hindi = sum(text.count(word) for word in HINDI_MARKERS)
    return marathi, hindi


def language_guess(marathi: int, hindi: int, dev: int, lat: int) -> str:
    if dev == 0 and lat:
        return "en"
    if marathi == 0 and hindi == 0:
        return "hi/mr (no markers)"
    if marathi > hindi * 1.3:
        return "mr"
    if hindi > marathi * 1.3:
        return "hi"
    return "hi+mr"


def wav_slice(path: str, offset: float, seconds: float) -> bytes:
    """Cut a slice of a 16-bit PCM wav without ffmpeg (the web image has none)."""
    import io
    import wave

    with wave.open(path, "rb") as src:
        rate = src.getframerate()
        src.setpos(min(int(offset * rate), src.getnframes() - 1))
        frames = src.readframes(int(seconds * rate))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(src.getnchannels())
            out.setsampwidth(src.getsampwidth())
            out.setframerate(rate)
            out.writeframes(frames)
        return buffer.getvalue()


# --------------------------------------------------------------------------- 1. services
print("[service health]")
health = audio_service.health()
if not health.get("asr", {}).get("ok"):
    # the 10s health timeout races the first diarizer load; give it one more shot
    health = audio_service.health()
denoise_health = health.get("denoise", {})
asr_health = health.get("asr", {})
check("denoise service up", denoise_health.get("ok"), denoise_health.get("engine"))
check("asr service up", asr_health.get("ok"), asr_health.get("engine"))
check("denoise runs the real hush weights", denoise_health.get("engine") == "hush",
      f"{denoise_health.get('engine')} ({denoise_health.get('load_error') or 'no error'})",
      soft=args.allow_fallback)
diarization = asr_health.get("diarization", {})
check("diarizer loaded the NeMo stack", diarization.get("engine") == "nemo",
      f"{diarization.get('engine')} ({diarization.get('error') or 'no error'})",
      soft=args.allow_fallback)
check("asr service will not substitute mock text",
      (asr_health.get("asr") or {}).get("backend") == "remote",
      (asr_health.get("asr") or {}).get("backend"), soft=args.allow_fallback)

print("\n[remote ASR endpoint]")
resolved = config_for("asr", use_cache=False)
print(f"  resolved: {resolved['model']} @ {resolved['base_url']} ({resolved['source']})")
try:
    headers = {"Authorization": f"Bearer {resolved['api_key']}"} if resolved.get("api_key") else {}
    served = [m["id"] for m in httpx.get(resolved["base_url"] + "/models", headers=headers,
                                         timeout=30, **resolved.get("httpx", {})).json().get("data", [])]
except Exception as exc:  # noqa: BLE001
    served = []
    check("remote ASR endpoint reachable", False, f"{type(exc).__name__}: {str(exc)[:120]}")
check("remote ASR serves the configured model", resolved["model"] in served, served)

# --------------------------------------------------------------------------- 2. the entry
print("\n[entry]")
created = False
if args.file:
    if not args.project:
        raise SystemExit("--file needs --project")
    project = Project.objects.get(pk=args.project)
    author = project.manager or project.created_by
    entry = TimelineEntry.objects.create(project=project, title=f"Audio test: {os.path.basename(args.file)}",
                                         kind="audio", author=author, body="", status="queued",
                                         include_in_brd=True)
    with open(args.file, "rb") as handle:
        entry.upload.save(os.path.basename(args.file), ContentFile(handle.read()), save=True)
    entry.original_name = os.path.basename(args.file)
    entry.save()
    created = True
else:
    entry = (TimelineEntry.objects.get(pk=args.entry) if args.entry
             else TimelineEntry.objects.filter(kind="audio").exclude(upload="").order_by("-id").first())
if entry is None:
    raise SystemExit("no audio entry found — pass --file PATH --project ID")
print(f"  entry #{entry.pk} · project {entry.project} · {entry.original_name or entry.upload.name}"
      f" · {'new' if created else 'reprocess'}")

if args.language:
    os.environ["ASR_LANGUAGE"] = args.language
    print(f"  forcing language={args.language}")

print("\n[pipeline: denoise -> diarize -> ASR per turn]")
pipeline.process_entry(entry, regenerate_brd=args.brd)
entry.refresh_from_db()
asset = entry.media_set.first()
check("entry did not fail", entry.status != "failed", entry.status)
check("media asset created", asset is not None)
check("denoised rendition stored", bool(asset and asset.denoised))
if asset:
    print(f"  denoise={asset.denoise_engine} asr={asset.asr_engine} diarization={asset.diarization_engine} "
          f"language={asset.language or 'auto'} turns={asset.turns} "
          f"duration={asset.duration_seconds:.1f}s processed_in={asset.processing_seconds:.1f}s")
    check("ASR ran on the remote model", asset.asr_engine == "qwen-remote", asset.asr_engine,
          soft=args.allow_fallback)
    check("diarization was the real engine", asset.diarization_engine == "nemo", asset.diarization_engine,
          soft=args.allow_fallback)
    check("denoise was the real engine", asset.denoise_engine == "hush", asset.denoise_engine,
          soft=args.allow_fallback)

segments = list(entry.segments.order_by("index"))
check("transcript segments stored", bool(segments), len(segments))
check("no placeholder text in the transcript",
      not any("[mock transcript" in s.text for s in segments),
      sum("[mock transcript" in s.text for s in segments))
check("no segment is empty", all(s.text.strip() for s in segments))
check("timings increase monotonically", all(b.start >= a.start for a, b in zip(segments, segments[1:])))
check("every segment has a positive span", all(s.end > s.start for s in segments))
speakers = list(entry.speakers.all())
check("speakers detected", len(speakers) >= 1, [s.label for s in speakers])
if asset and asset.duration_seconds:
    covered = sum(s.end - s.start for s in segments)
    ratio = covered / asset.duration_seconds
    check("transcript covers most of the recording", ratio > 0.5, f"{ratio:.0%} of {asset.duration_seconds:.0f}s")

# --------------------------------------------------------------------------- 3. language report
print("\n[language of the returned text]")
totals = {"devanagari": 0, "latin": 0, "mixed": 0, "none": 0}
dev_all = lat_all = mar_all = hin_all = 0
for segment in segments:
    kind = classify_script(segment.text)
    totals[kind] += 1
    dev, lat = scripts_of(segment.text)
    marathi, hindi = marker_hits(segment.text)
    dev_all, lat_all, mar_all, hin_all = dev_all + dev, lat_all + lat, mar_all + marathi, hin_all + hindi

english_tokens = sorted({w for s in segments for w in ENGLISH_HINT.findall(s.text)})
mix_ratio = totals["mixed"] / len(segments) if segments else 0
print(f"  segments: {len(segments)} · devanagari-only {totals['devanagari']} · latin-only {totals['latin']}"
      f" · code-mixed {totals['mixed']} ({mix_ratio:.0%})")
print(f"  letters: devanagari {dev_all} · latin {lat_all}")
print(f"  markers: marathi {mar_all} · hindi {hin_all} -> overall guess "
      f"{language_guess(mar_all, hin_all, dev_all, lat_all)}")
if english_tokens:
    print(f"  latin tokens kept verbatim ({len(english_tokens)}): {', '.join(english_tokens[:15])}"
          f"{' …' if len(english_tokens) > 15 else ''}")
check("the model returned real script, not placeholders", dev_all + lat_all > 0, f"{dev_all + lat_all} letters")
check("transcript is not a single repeated line", len({s.text for s in segments}) > 1
      if len(segments) > 1 else True, len({s.text for s in segments}))

print("\n  per-speaker sample")
for speaker in speakers:
    lines = [s for s in segments if s.speaker_label == speaker.label]
    if not lines:
        continue
    joined = " ".join(s.text for s in lines)
    marathi, hindi = marker_hits(joined)
    dev, lat = scripts_of(joined)
    print(f"   {speaker.label}: {len(lines)} turns · {speaker.talk_seconds:.0f}s talk · "
          f"{language_guess(marathi, hindi, dev, lat)} · {classify_script(joined)}")
    print(f"     {lines[0].text[:160]}")

# --------------------------------------------------------------------------- 4. language matrix
print(f"\n[language hint matrix · {args.matrix_slices} slice(s) of {args.matrix_seconds:.0f}s]")
denoised_path = asset.denoised.path if asset and asset.denoised else None
if denoised_path and os.path.exists(denoised_path):
    span = max(0.0, (asset.duration_seconds or 0) - args.matrix_offset - args.matrix_seconds)
    offsets = [args.matrix_offset + span * i / max(1, args.matrix_slices - 1) if args.matrix_slices > 1
               else args.matrix_offset for i in range(args.matrix_slices)]
    settings = audio_service.asr_settings()
    tally = {}
    for offset in offsets:
        slice_bytes = wav_slice(denoised_path, offset, args.matrix_seconds)
        print(f"  slice @ {offset:.0f}s")
        for hint in ["", "hi", "mr", "en"]:
            label = hint or "auto"
            try:
                reply = httpx.post(
                    f"{audio_service.ASR_URL}/audio/transcriptions",
                    files={"file": ("slice.wav", slice_bytes, "audio/wav")},
                    data={"model": settings.get("model", ""), "response_format": "verbose_json",
                          "diarize": "false", "language": hint, "backend": "remote",
                          "asr_base_url": settings.get("base_url", ""), "asr_api_key": settings.get("api_key", ""),
                          "asr_model": settings.get("model", ""), "ca_cert_path": settings.get("ca_cert_path", ""),
                          "verify_ssl": "0" if settings.get("verify_ssl") is False else "1"},
                    timeout=600,
                )
                payload = reply.json() if reply.status_code < 400 else {}
                text = (payload.get("text") or "").strip()
                ok = reply.status_code == 200 and payload.get("asr_engine") == "qwen-remote" and bool(text)
                check(f"  language={label:4} transcribed", ok,
                      f"{reply.status_code} {payload.get('asr_engine')} {classify_script(text)}")
                marathi, hindi = marker_hits(text)
                dev, lat = scripts_of(text)
                row = tally.setdefault(label, {"mr": 0, "hi": 0, "dev": 0, "lat": 0, "empty": 0})
                row["mr"] += marathi
                row["hi"] += hindi
                row["dev"] += dev
                row["lat"] += lat
                row["empty"] += 0 if text else 1
                print(f"       {text[:170] or '(empty)'}")
            except Exception as exc:  # noqa: BLE001
                check(f"  language={label} request", False, f"{type(exc).__name__}: {str(exc)[:120]}")
    print("\n  hint     devanagari  latin  mr-markers  hi-markers  empty")
    for label, row in tally.items():
        print(f"  {label:8} {row['dev']:10}  {row['lat']:5}  {row['mr']:10}  {row['hi']:10}  {row['empty']:5}")
else:
    check("denoised wav available for the language matrix", False, denoised_path or "missing")

# --------------------------------------------------------------------------- 5. summary
print(f"\nentry #{entry.pk} status: {entry.status}")
if entry.status == "awaiting_speakers":
    print("  (expected: diarizer labels need names before the BRD is rewritten — "
          f"/entries/{entry.pk}/)")
print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILURES:\n" + "\n".join(fails)))
if warns:
    print(f"{len(warns)} warning(s): " + "; ".join(warns))
raise SystemExit(1 if fails else 0)
