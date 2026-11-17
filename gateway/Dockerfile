FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ui.html shim.js ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /gateway .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates && adduser -D -u 10001 app && mkdir /data && chown app /data
COPY --from=build /gateway /gateway
USER app
VOLUME /data
EXPOSE 8443
ENTRYPOINT ["/gateway"]
