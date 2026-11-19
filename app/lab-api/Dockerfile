# lab-api 이미지 — 빌드는 Go 공식 이미지, 실행은 distroless static(셸·패키지 관리자 없음, nonroot 65532)
# 베이스 이미지는 태그 + digest 고정 (Renovate 가 함께 갱신). 빌드·스캔·서명은 .github/workflows/lab-api.yml
FROM golang:1.26.11-alpine@sha256:ecb67f987aa1a57794c91d19ade7ae0209f2f0f5496aaef9e6ae85ddf92d02ab AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download && go mod verify
COPY *.go ./
ARG VERSION=dev
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags "-s -w -X main.version=${VERSION}" -o /out/lab-api .

FROM gcr.io/distroless/static-debian13:nonroot@sha256:aafe993665bf6200db38a02c11a9351e782a84b6a45e7f33b6695638c88e3931
COPY --from=build /out/lab-api /lab-api
USER 65532:65532
EXPOSE 8080 9090
ENTRYPOINT ["/lab-api"]
