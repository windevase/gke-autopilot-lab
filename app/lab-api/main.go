// lab-api: SRE 실습용 샘플 서비스 (SLO·canary·공급망 보안의 대상)
//
//	GET /            서비스 정보(JSON)
//	GET /api/items   샘플 API — SLO 대상 경로
//	GET /healthz     liveness  /  GET /readyz  readiness(종료 중이면 503)
//	GET /metrics     Prometheus 지표 (별도 포트 9090 — 서비스 트래픽과 분리)
//	`lab-api loadgen` 상시 트래픽 생성 모드 (loadgen.go)
//
// 장애 주입(canary 자동 롤백 시연용, 환경 변수):
//
//	FAULT_ERROR_RATE  0~1   /api/items 응답 중 이 비율을 500 으로
//	FAULT_LATENCY_MS  정수  /api/items 응답에 이만큼 지연 추가
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math/rand/v2"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// version 은 빌드 때 -ldflags "-X main.version=..." 로 주입
var version = "dev"

type config struct {
	addr, metricsAddr string
	errorRate         float64
	latency           time.Duration
}

func loadConfig() config {
	c := config{addr: ":8080", metricsAddr: ":9090"}
	if v, err := strconv.ParseFloat(os.Getenv("FAULT_ERROR_RATE"), 64); err == nil && v >= 0 && v <= 1 {
		c.errorRate = v
	}
	if v, err := strconv.Atoi(os.Getenv("FAULT_LATENCY_MS")); err == nil && v > 0 {
		c.latency = time.Duration(v) * time.Millisecond
	}
	return c
}

type server struct {
	cfg      config
	ready    atomic.Bool
	requests *prometheus.CounterVec
	duration *prometheus.HistogramVec
	rnd      func() float64
}

func newServer(cfg config, reg prometheus.Registerer) *server {
	s := &server{
		cfg: cfg,
		rnd: rand.Float64,
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "http_requests_total",
			Help: "처리한 HTTP 요청 수",
		}, []string{"handler", "method", "code"}),
		// SLO 지연 기준(300ms)을 경계로 갖는 버킷
		duration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "http_request_duration_seconds",
			Help:    "HTTP 요청 처리 시간",
			Buckets: []float64{0.025, 0.05, 0.1, 0.3, 0.5, 1, 2.5},
		}, []string{"handler", "method"}),
	}
	reg.MustRegister(s.requests, s.duration)
	reg.MustRegister(prometheus.NewGaugeFunc(prometheus.GaugeOpts{
		Name:        "lab_api_build_info",
		Help:        "빌드 정보 (값은 항상 1)",
		ConstLabels: prometheus.Labels{"version": version},
	}, func() float64 { return 1 }))
	s.ready.Store(true)
	return s
}

// statusRecorder 는 응답 코드를 지표 레이블로 남기기 위해 기록한다
type statusRecorder struct {
	http.ResponseWriter
	code int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.code = code
	r.ResponseWriter.WriteHeader(code)
}

// instrument 는 handler 이름별로 요청 수·처리 시간을 기록한다
func (s *server) instrument(name string, h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, code: http.StatusOK}
		h(rec, r)
		s.duration.WithLabelValues(name, r.Method).Observe(time.Since(start).Seconds())
		s.requests.WithLabelValues(name, r.Method, strconv.Itoa(rec.code)).Inc()
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func (s *server) handleRoot(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "not found"})
		return
	}
	host, _ := os.Hostname()
	writeJSON(w, http.StatusOK, map[string]string{"service": "lab-api", "version": version, "pod": host})
}

func (s *server) handleItems(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
		return
	}
	if s.cfg.latency > 0 {
		select {
		case <-time.After(s.cfg.latency):
		case <-r.Context().Done():
			return
		}
	}
	if s.cfg.errorRate > 0 && s.rnd() < s.cfg.errorRate {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "injected fault"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"items":   []map[string]any{{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}, {"id": 3, "name": "gamma"}},
		"version": version,
	})
}

func (s *server) handleReady(w http.ResponseWriter, _ *http.Request) {
	if !s.ready.Load() {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "shutting down"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.instrument("root", s.handleRoot))
	mux.HandleFunc("/api/items", s.instrument("items", s.handleItems))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/readyz", s.handleReady)
	return mux
}

func main() {
	if len(os.Args) > 1 && os.Args[1] == "loadgen" {
		loadgenMain()
		return
	}
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	cfg := loadConfig()
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	s := newServer(cfg, reg)

	app := &http.Server{Addr: cfg.addr, Handler: s.routes(), ReadHeaderTimeout: 5 * time.Second}
	metricsMux := http.NewServeMux()
	metricsMux.Handle("/metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{Registry: reg}))
	metrics := &http.Server{Addr: cfg.metricsAddr, Handler: metricsMux, ReadHeaderTimeout: 5 * time.Second}

	for _, srv := range []*http.Server{app, metrics} {
		go func(srv *http.Server) {
			if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
				slog.Error("listen failed", "addr", srv.Addr, "err", err)
				os.Exit(1)
			}
		}(srv)
	}
	slog.Info("started", "version", version, "addr", cfg.addr, "fault_error_rate", cfg.errorRate, "fault_latency", cfg.latency.String())

	// 종료: readiness 를 먼저 내려 엔드포인트에서 빠진 뒤 진행 중 요청을 마무리 (Spot 종료 유예 25s 안)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()
	<-ctx.Done()
	s.ready.Store(false)
	slog.Info("shutting down")
	time.Sleep(5 * time.Second)
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	_ = app.Shutdown(shutdownCtx)
	_ = metrics.Shutdown(shutdownCtx)
	slog.Info("stopped")
}
