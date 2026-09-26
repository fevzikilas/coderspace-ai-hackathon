# Doğrulanmış isteklerin arka servise iletimi
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host $host;
proxy_set_header X-Real-IP $client_ip;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $fwd_proto;
proxy_read_timeout 180s;   # LLM'li risk değerlendirmesi uzun sürebilir
# Güvenlik başlıklarını auth-gate.inc ekler; arka servisinkiler çift gelmesin
proxy_hide_header X-Content-Type-Options;
proxy_hide_header X-Frame-Options;
proxy_hide_header Referrer-Policy;
