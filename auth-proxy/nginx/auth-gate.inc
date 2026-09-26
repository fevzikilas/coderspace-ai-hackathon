# Her server bloğuna eklenir: oturum doğrulama, giriş sayfası ve yetkisiz yanıtı.
client_max_body_size 20m;   # web-ui base64 görüntü yükleme (~14 MB gövde)
server_tokens off;

add_header X-Content-Type-Options nosniff always;
add_header X-Frame-Options DENY always;
add_header Referrer-Policy no-referrer always;

# Proxy'nin kendi sağlık ucu (arka servise gitmez, bilgi sızdırmaz)
location = /healthz {
    access_log off;
    default_type text/plain;
    return 200 'ok';
}

# auth_request iç alt isteği: yalnızca çerez gider, gövde gitmez
location = /_auth {
    internal;
    proxy_pass http://auth_svc/auth/verify;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Cookie $http_cookie;
}

# Giriş/çıkış sayfaları (korumasız; kaba kuvvet sınırlı)
location /auth/ {
    limit_req zone=auth_login burst=10 nodelay;
    proxy_pass http://auth_svc;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $client_ip;
    proxy_set_header X-Forwarded-Proto $fwd_proto;
}

# auth_request 401 → auth-svc karar verir: tarayıcıya 302 /auth/login?next=…, API istemcisine 401 JSON
error_page 401 = @unauthorized;
location @unauthorized {
    rewrite ^ /auth/unauthorized break;
    proxy_pass http://auth_svc;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_method GET;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Original-URI $request_uri;
}
