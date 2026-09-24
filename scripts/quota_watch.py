"""GOAL API kota bekleyicisi: python scripts/quota_watch.py

Günlük kota döndüğünde X-RateLimit-* başlıklarını okur, sıfırlama saatini
yazar ve durur. Kota doluyken sağlayıcı 429 döner; o istekler kotaya yazılmaz.

- Anahtar yalnızca GOAL_API_KEY ortam değişkeninden ya da depo dışındaki
  anahtar dosyasından okunur; loga, rapora veya veriye yazılmaz.
- --aralik bekleme süresini saniye cinsinden belirtir (varsayılan 600).
- --sinir, kota dönmeden geçilecek toplam süreyi sınırlar (varsayılan 0 = sınırsız).
- Kota hiç dönmezse çıkış kodu 3 döner.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_goal_api as probe  # noqa: E402  (yol, modul içe aktarılmadan önce açılır)

BASE = "https://api.goal-api.com/v1"
TIMEOUT = 20


def read_quota(key):
    """Tek istek atar; (durum kodu, yanıt başlıkları) döndürür. Durum None ise ağ hatası."""
    request = Request(
        f"{BASE}/leagues?limit=1",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "LigPuan-veri-guncelleyici",
        },
    )
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers)
    except HTTPError as error:
        return error.code, dict(error.headers or {})
    except (URLError, TimeoutError, OSError):
        return None, {}


def main():
    parser = argparse.ArgumentParser(description="GOAL API günlük kotası dönene kadar yoklar.")
    parser.add_argument("--aralik", type=int, default=600, help="iki yoklama arasındaki saniye")
    parser.add_argument("--sinir", type=int, default=0, help="toplam bekleme sınırı, saniye (0 = sınırsız)")
    args = parser.parse_args()

    key, source = probe.load_key()
    if not key:
        print("GOAL_API_KEY tanımlı değil ve depo dışı anahtar dosyası yok.", file=sys.stderr)
        return 2
    print(f"anahtar kaynağı: {source} (değer yazılmadı)")

    started = time.time()
    while True:
        status, headers = read_quota(key)
        elapsed = int(time.time() - started)

        if status == 200:
            probe.print_quota(headers)
            print(f"kota döndü; yoklama {elapsed} sürdü")
            return 0
        if status is None:
            print(f"[{elapsed} sn] ağ hatası, yeniden denenecek")
        elif status == 429:
            print(f"[{elapsed} sn] kota dolu (429), bekleniyor")
        elif 500 <= status < 600:
            print(f"[{elapsed} sn] geçici sunucu hatası {status}, bekleniyor")
        else:
            print(f"beklenmeyen durum {status}; anahtarı ve adresi kontrol edin", file=sys.stderr)
            return 1

        if args.sinir and elapsed >= args.sinir:
            print(f"süre sınırı ({args.sinir} sn) doldu; kota dönmedi", file=sys.stderr)
            return 3
        time.sleep(args.aralik)


if __name__ == "__main__":
    raise SystemExit(main())
