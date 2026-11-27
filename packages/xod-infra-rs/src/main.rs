use axum::{
    body::Body,
    extract::State,
    http::{header, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures_util::StreamExt;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    process::Command,
    sync::{Arc, Mutex},
    time::{Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Clone, Serialize)]
struct RunMetric {
    model: String,
    backend: String,
    ttft_ms: f64,
    decode_tps: f64,
    prefix_hit: bool,
    ts: u64,
}

struct Telemetry {
    requests: u64,
    hits: u64,
    seen_prefixes: VecDeque<String>,
    recent: VecDeque<RunMetric>,
}

struct AppState {
    client: reqwest::Client,
    telemetry: Mutex<Telemetry>,
}

fn sysctl(key: &str) -> String {
    Command::new("sysctl")
        .args(["-n", key])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

fn estimate_bandwidth(brand: &str) -> u32 {
    let b = brand.to_lowercase();
    let table = [
        ("m4 max", 546), ("m4 pro", 273), ("m4", 120),
        ("m3 ultra", 800), ("m3 max", 400), ("m3 pro", 150), ("m3", 100),
        ("m2 ultra", 800), ("m2 max", 400), ("m2 pro", 200), ("m2", 100),
        ("m1 ultra", 800), ("m1 max", 400), ("m1 pro", 200), ("m1", 68),
    ];
    for (k, v) in table {
        if b.contains(k) {
            return v;
        }
    }
    if b.contains("apple") { 100 } else { 50 }
}

async fn probe_url(client: &reqwest::Client, url: &str) -> bool {
    client
        .get(url)
        .timeout(std::time::Duration::from_millis(700))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

async fn pick_upstream(client: &reqwest::Client) -> (&'static str, &'static str) {
    if probe_url(client, "http://127.0.0.1:8080/v1/models").await {
        return ("mlx", "http://127.0.0.1:8080/v1");
    }
    if probe_url(client, "http://127.0.0.1:1234/v1/models").await {
        return ("lmstudio", "http://127.0.0.1:1234/v1");
    }
    if probe_url(client, "http://127.0.0.1:11434/api/tags").await {
        return ("ollama", "http://127.0.0.1:11434/v1");
    }
    ("ollama", "http://127.0.0.1:11434/v1")
}

fn prefix_hash(messages: &Value) -> String {
    let arr = match messages.as_array() {
        Some(a) if !a.is_empty() => a,
        _ => return String::new(),
    };
    let head: Vec<&Value> = arr.iter().take(2).collect();
    let s = serde_json::to_string(&head).unwrap_or_default();
    let mut hasher = Sha256::new();
    hasher.update(s.as_bytes());
    let digest = hasher.finalize();
    digest.iter().take(8).map(|b| format!("{:02x}", b)).collect()
}

fn now_ts() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

async fn api_status(State(st): State<Arc<AppState>>) -> impl IntoResponse {
    let brand = sysctl("machdep.cpu.brand_string");
    let mem_bytes: u64 = sysctl("hw.memsize").parse().unwrap_or(0);
    let ram_gb = ((mem_bytes as f64 / (1024.0 * 1024.0 * 1024.0)) * 10.0).round() / 10.0;
    let bw = estimate_bandwidth(&brand);
    let (up_name, up_url) = pick_upstream(&st.client).await;

    let (reqs, hit_rate, avg_tps, avg_ttft, recent) = {
        let t = st.telemetry.lock().unwrap();
        let hr = if t.requests > 0 { ((t.hits as f64 / t.requests as f64) * 1000.0).round() / 10.0 } else { 0.0 };
        let tps_vals: Vec<f64> = t.recent.iter().map(|r| r.decode_tps).filter(|v| *v > 0.0).collect();
        let avg_t = if !tps_vals.is_empty() {
            ((tps_vals.iter().sum::<f64>() / tps_vals.len() as f64) * 10.0).round() / 10.0
        } else { 0.0 };
        let ttft_vals: Vec<f64> = t.recent.iter().map(|r| r.ttft_ms).filter(|v| *v > 0.0).collect();
        let avg_ms = if !ttft_vals.is_empty() {
            ((ttft_vals.iter().sum::<f64>() / ttft_vals.len() as f64) * 10.0).round() / 10.0
        } else { 0.0 };
        (t.requests, hr, avg_t, avg_ms, t.recent.iter().cloned().collect::<Vec<_>>())
    };

    Json(json!({
        "engine": "rust",
        "version": env!("CARGO_PKG_VERSION"),
        "upstream": { "name": up_name, "url": up_url },
        "hardware": {
            "chip": brand,
            "ram_gb": ram_gb,
            "memory_bandwidth_gbps": bw,
            "apple_silicon": cfg!(all(target_os = "macos", target_arch = "aarch64")),
        },
        "telemetry": {
            "requests": reqs,
            "prefix_cache_hit_rate": hit_rate,
            "avg_decode_tps": avg_tps,
            "avg_ttft_ms": avg_ttft,
            "recent": recent,
        }
    }))
}

async fn v1_models(State(st): State<Arc<AppState>>) -> Response {
    let (_, up_url) = pick_upstream(&st.client).await;
    match st.client.get(format!("{}/models", up_url)).send().await {
        Ok(resp) => {
            let status = StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::OK);
            let body = resp.text().await.unwrap_or_else(|_| "{}".into());
            (status, [(header::CONTENT_TYPE, "application/json")], body).into_response()
        }
        Err(e) => (StatusCode::SERVICE_UNAVAILABLE, Json(json!({ "error": e.to_string() }))).into_response(),
    }
}

async fn v1_chat_completions(State(st): State<Arc<AppState>>, Json(mut payload): Json<Value>) -> Response {
    let (up_name, up_url) = pick_upstream(&st.client).await;
    let model = payload.get("model").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let stream = payload.get("stream").and_then(|v| v.as_bool()).unwrap_or(false);
    let ph = payload.get("messages").map(prefix_hash).unwrap_or_default();

    let cache_hit = {
        let mut t = st.telemetry.lock().unwrap();
        t.requests += 1;
        let hit = !ph.is_empty() && t.seen_prefixes.contains(&ph);
        if hit {
            t.hits += 1;
        } else if !ph.is_empty() {
            if t.seen_prefixes.len() >= 256 { t.seen_prefixes.pop_front(); }
            t.seen_prefixes.push_back(ph);
        }
        hit
    };

    if up_name == "ollama" {
        if let Some(obj) = payload.as_object_mut() {
            obj.entry("keep_alive").or_insert(json!("30m"));
        }
    }

    let t0 = Instant::now();
    let url = format!("{}/chat/completions", up_url);
    let resp = match st.client.post(&url).json(&payload).send().await {
        Ok(r) => r,
        Err(e) => return (StatusCode::BAD_GATEWAY, Json(json!({ "error": e.to_string() }))).into_response(),
    };

    if stream {
        let st_clone = st.clone();
        let up_s = up_name.to_string();
        let byte_stream = resp.bytes_stream();
        let mut first_at: Option<Instant> = None;
        let mut chunks: u64 = 0;

        let mapped = async_stream_chunks(byte_stream, move |is_end| {
            if !is_end {
                if first_at.is_none() { first_at = Some(Instant::now()); }
                chunks += 1;
            } else {
                let end = Instant::now();
                let ttft_ms = ((first_at.unwrap_or(end).duration_since(t0).as_secs_f64()) * 10000.0).round() / 10.0;
                let gen_s = end.duration_since(first_at.unwrap_or(t0)).as_secs_f64().max(0.001);
                let tps = ((chunks as f64 / gen_s) * 10.0).round() / 10.0;
                let mut t = st_clone.telemetry.lock().unwrap();
                if t.recent.len() >= 50 { t.recent.pop_front(); }
                t.recent.push_back(RunMetric {
                    model: model.clone(),
                    backend: up_s.clone(),
                    ttft_ms,
                    decode_tps: tps,
                    prefix_hit: cache_hit,
                    ts: now_ts(),
                });
            }
        });

        return Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, "text/event-stream")
            .body(Body::from_stream(mapped))
            .unwrap();
    }

    let status = StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::OK);
    let text = resp.text().await.unwrap_or_else(|_| "{}".into());
    let dur = t0.elapsed().as_secs_f64().max(0.001);
    let comp_toks = serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|v| v.pointer("/usage/completion_tokens").and_then(|n| n.as_u64()))
        .unwrap_or(0);
    {
        let mut t = st.telemetry.lock().unwrap();
        if t.recent.len() >= 50 { t.recent.pop_front(); }
        t.recent.push_back(RunMetric {
            model,
            backend: up_name.to_string(),
            ttft_ms: (dur * 10000.0).round() / 10.0,
            decode_tps: if comp_toks > 0 { ((comp_toks as f64 / dur) * 10.0).round() / 10.0 } else { 0.0 },
            prefix_hit: cache_hit,
            ts: now_ts(),
        });
    }
    (status, [(header::CONTENT_TYPE, "application/json")], text).into_response()
}

fn async_stream_chunks<S, F>(
    mut inner: S,
    mut on_event: F,
) -> impl futures_util::Stream<Item = Result<axum::body::Bytes, std::io::Error>> + Send + 'static
where
    S: futures_util::Stream<Item = Result<axum::body::Bytes, reqwest::Error>> + Unpin + Send + 'static,
    F: FnMut(bool) + Send + 'static,
{
    futures_util::stream::unfold((inner, on_event, false), |(mut s, mut cb, done)| async move {
        if done {
            return None;
        }
        match s.next().await {
            Some(Ok(bytes)) => {
                cb(false);
                Some((Ok(bytes), (s, cb, false)))
            }
            Some(Err(e)) => {
                cb(true);
                Some((Err(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())), (s, cb, true)))
            }
            None => {
                cb(true);
                None
            }
        }
    })
}

async fn dashboard() -> Html<&'static str> {
    Html(r#"<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Xod Infra (Rust) — Apple Silicon Acceleration</title>
<style>body{margin:0;background:#0d0f14;color:#e8ecf2;font-family:-apple-system,BlinkMacSystemFont,Inter,sans-serif;padding:28px}.wrap{max-width:860px;margin:0 auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:18px}.card{background:#161a23;border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:16px}.k{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#94a3b8}.v{font-size:22px;font-weight:700;margin-top:4px;color:#fff}.sub{color:#94a3b8;font-size:13px;margin-top:4px}code{background:#0b0d12;padding:2px 6px;border-radius:6px;color:#93c5fd}</style>
</head><body><div class="wrap"><h1>⚡ Xod Infra <span style="font-size:13px;background:#f59e0b;color:#111;padding:3px 8px;border-radius:999px;vertical-align:middle">RUST NATIVE</span></h1>
<div class="sub">Zero-overhead Apple Silicon OpenAI gateway · Point Xod to <code>http://127.0.0.1:8799/v1</code></div>
<div class="grid" id="stats"></div></div>
<script>
async function load(){const d=await(await fetch('/api/status')).json();const h=d.hardware,t=d.telemetry;
document.getElementById('stats').innerHTML=`
<div class="card"><div class="k">Hardware</div><div class="v">${h.chip}</div><div class="sub">${h.ram_gb} GB Unified · ~${h.memory_bandwidth_gbps} GB/s</div></div>
<div class="card"><div class="k">Active Backend</div><div class="v">${d.upstream.name.toUpperCase()}</div><div class="sub">${d.upstream.url}</div></div>
<div class="card"><div class="k">Live Speed</div><div class="v">${t.avg_decode_tps||0} tok/s</div><div class="sub">TTFT ${t.avg_ttft_ms||0} ms · Cache ${t.prefix_cache_hit_rate}%</div></div>`;}
load();setInterval(load,4000);
</script></body></html>"#)
}

#[tokio::main]
async fn main() {
    let port: u16 = std::env::args()
        .skip_while(|a| a != "--port")
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(8799);

    let state = Arc::new(AppState {
        client: reqwest::Client::builder().build().expect("http client"),
        telemetry: Mutex::new(Telemetry {
            requests: 0,
            hits: 0,
            seen_prefixes: VecDeque::with_capacity(256),
            recent: VecDeque::with_capacity(50),
        }),
    });

    let app = Router::new()
        .route("/", get(dashboard))
        .route("/api/status", get(api_status))
        .route("/v1/models", get(v1_models))
        .route("/v1/chat/completions", post(v1_chat_completions))
        .with_state(state);

    let addr = format!("127.0.0.1:{}", port);
    println!("⚡ Xod Infra (Rust native) listening on http://{}", addr);
    let listener = tokio::net::TcpListener::bind(&addr).await.expect("bind");
    axum::serve(listener, app).await.expect("serve");
}
