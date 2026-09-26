"""Test kimlik bilgileri: her koşuda RASTGELE üretilir, repoda hiçbir şifre yazılı değildir.
app.main içe aktarılırken env'i okuduğu için bu dosya (pytest onu test modüllerinden önce yükler) env'i ÖNCE doldurur."""
import os
import secrets

os.environ["AUTH_USERNAME"] = "test-kullanici"
os.environ["AUTH_PASSWORD"] = secrets.token_urlsafe(18)
os.environ["AUTH_SECRET"] = secrets.token_hex(32)
