package main

// loadgen 모드: `lab-api loadgen` — SLO 지표가 비지 않도록 서비스에 작은 상시 트래픽을 보낸다
// (같은 서명된 이미지를 쓰므로 네임스페이스 안 이미지가 모두 서명된 것만 남는다)
//   LOADGEN_TARGET  호출 주소 (기본 http://lab-api/api/items)
//   LOADGEN_RPS     초당 요청 수 (기본 2, 최대 50)

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"
)

func loadgenConfig() (string, float64) {
	target := os.Getenv("LOADGEN_TARGET")
	if target == "" {
		target = "http://lab-api/api/items"
	}
	rps := 2.0
	if v, err := strconv.ParseFloat(os.Getenv("LOADGEN_RPS"), 64); err == nil && v > 0 && v <= 50 {
		rps = v
	}
	return target, rps
}

// runLoadgen 은 ctx 가 끝날 때까지 target 을 rps 속도로 호출하고 (성공, 실패) 수를 돌려준다
func runLoadgen(ctx context.Context, target string, rps float64, report time.Duration) (ok, fail int) {
	// keep-alive 끔: 요청마다 새 연결 → Service(kube-proxy, 연결 단위 분산)가 파드별로 고르게 나눔.
	// 연결을 재사용하면 모든 요청이 한 파드로만 가서 canary 가 트래픽을 못 받는다 (2026-11-19 시연 실패 원인)
	client := &http.Client{Timeout: 5 * time.Second, Transport: &http.Transport{DisableKeepAlives: true}}
	tick := time.NewTicker(time.Duration(float64(time.Second) / rps))
	defer tick.Stop()
	rep := time.NewTicker(report)
	defer rep.Stop()
	for {
		select {
		case <-ctx.Done():
			return ok, fail
		case <-rep.C:
			slog.Info("loadgen", "target", target, "ok", ok, "fail", fail)
		case <-tick.C:
			req, _ := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
			resp, err := client.Do(req)
			if err != nil {
				if ctx.Err() == nil {
					fail++
				}
				continue
			}
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			if resp.StatusCode < 500 {
				ok++
			} else {
				fail++
			}
		}
	}
}

func loadgenMain() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	target, rps := loadgenConfig()
	slog.Info("loadgen started", "version", version, "target", target, "rps", rps)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()
	ok, fail := runLoadgen(ctx, target, rps, time.Minute)
	slog.Info("loadgen stopped", "ok", ok, "fail", fail)
}
