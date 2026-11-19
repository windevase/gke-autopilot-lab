package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

func newTestServer(cfg config) *server {
	return newServer(cfg, prometheus.NewRegistry())
}

func get(t *testing.T, h http.Handler, path string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, path, nil))
	return rec
}

func TestItemsOK(t *testing.T) {
	s := newTestServer(config{})
	rec := get(t, s.routes(), "/api/items")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"alpha"`) {
		t.Fatalf("code=%d body=%s", rec.Code, rec.Body.String())
	}
	if got := testutil.ToFloat64(s.requests.WithLabelValues("items", "GET", "200")); got != 1 {
		t.Fatalf("requests_total{code=200}=%v, want 1", got)
	}
}

func TestFaultInjectionError(t *testing.T) {
	s := newTestServer(config{errorRate: 0.5})
	s.rnd = func() float64 { return 0.1 } // 0.1 < 0.5 → 오류
	if rec := get(t, s.routes(), "/api/items"); rec.Code != http.StatusInternalServerError {
		t.Fatalf("code=%d, want 500", rec.Code)
	}
	if got := testutil.ToFloat64(s.requests.WithLabelValues("items", "GET", "500")); got != 1 {
		t.Fatalf("requests_total{code=500}=%v, want 1", got)
	}
	s.rnd = func() float64 { return 0.9 } // 0.9 >= 0.5 → 정상
	if rec := get(t, s.routes(), "/api/items"); rec.Code != http.StatusOK {
		t.Fatalf("code=%d, want 200", rec.Code)
	}
}

func TestFaultInjectionLatency(t *testing.T) {
	s := newTestServer(config{latency: 30 * time.Millisecond})
	start := time.Now()
	get(t, s.routes(), "/api/items")
	if d := time.Since(start); d < 30*time.Millisecond {
		t.Fatalf("latency=%v, want >=30ms", d)
	}
}

func TestReadyz(t *testing.T) {
	s := newTestServer(config{})
	if rec := get(t, s.routes(), "/readyz"); rec.Code != http.StatusOK {
		t.Fatalf("ready code=%d", rec.Code)
	}
	s.ready.Store(false)
	if rec := get(t, s.routes(), "/readyz"); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("shutting down code=%d, want 503", rec.Code)
	}
}

func TestNotFound(t *testing.T) {
	s := newTestServer(config{})
	if rec := get(t, s.routes(), "/nope"); rec.Code != http.StatusNotFound {
		t.Fatalf("code=%d, want 404", rec.Code)
	}
}

func TestLoadConfig(t *testing.T) {
	t.Setenv("FAULT_ERROR_RATE", "0.2")
	t.Setenv("FAULT_LATENCY_MS", "150")
	c := loadConfig()
	if c.errorRate != 0.2 || c.latency != 150*time.Millisecond {
		t.Fatalf("got %+v", c)
	}
	t.Setenv("FAULT_ERROR_RATE", "7") // 범위 밖은 무시
	if c := loadConfig(); c.errorRate != 0 {
		t.Fatalf("out of range accepted: %v", c.errorRate)
	}
}

func TestLoadgen(t *testing.T) {
	s := newTestServer(config{errorRate: 1})
	s.rnd = func() float64 { return 0 } // /api/items 는 전부 500
	ts := httptest.NewServer(s.routes())
	defer ts.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	ok, fail := runLoadgen(ctx, ts.URL+"/", 50, time.Hour)
	if ok == 0 || fail != 0 {
		t.Fatalf("root: ok=%d fail=%d", ok, fail)
	}
	ctx2, cancel2 := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel2()
	ok, fail = runLoadgen(ctx2, ts.URL+"/api/items", 50, time.Hour)
	if ok != 0 || fail == 0 {
		t.Fatalf("items(500): ok=%d fail=%d", ok, fail)
	}
}

func TestLoadgenConfig(t *testing.T) {
	t.Setenv("LOADGEN_TARGET", "")
	t.Setenv("LOADGEN_RPS", "999")
	if target, rps := loadgenConfig(); target != "http://lab-api/api/items" || rps != 2 {
		t.Fatalf("got %s %v", target, rps)
	}
}
