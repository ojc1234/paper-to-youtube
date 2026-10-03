
// ---- paper-to-youtube (FastAPI :8090) 를 /paper/ 아래로 연결 ----
const P2Y_PORT = 8090;
app.use((req, res, next) => {
  if (req.path === '/paper') return res.redirect(301, '/paper/');
  if (!req.path.startsWith('/paper/')) return next();
  const opts = { hostname: '127.0.0.1', port: P2Y_PORT, path: req.url.replace(/^\/paper/, '') || '/',
                 method: req.method, headers: req.headers };
  const pr = http.request(opts, (r) => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
  pr.on('error', (e) => {
    if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('paper-to-youtube 서버(8090)가 응답하지 않습니다: ' + e.message);
  });
  res.on('close', () => pr.destroy());   // SSE 연결이 끊기면 정리
  req.pipe(pr);
});
