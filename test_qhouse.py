"""Tes Q-House (python test_qhouse.py). Server sungguhan di port acak + DB sementara; alur driver -> sorter -> loket,
bermasalah -> supervisor, gerbang staf, batas peran, kedaluwarsa, dan data publik yang disamarkan."""
import json, os, sys, tempfile, threading, time, urllib.request, urllib.error

tmp = tempfile.mkdtemp()
os.environ.update(QH_DATA=tmp, GATE_SECRET="g" * 32, QH_REQUIRE_GATE="1", QH_PUBLIC_URL="http://qh.test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

server.init()
srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]
GAGAL = []


def check(ok, msg, info=""):
    print(("  [ok] " if ok else "  [GAGAL] ") + msg + ("" if ok else " :: " + str(info)))
    if not ok:
        GAGAL.append(msg)


def req(method, path, body=None, role=None, admin=False, ip="10.0.0.1"):
    h = {"Content-Type": "application/json", "X-Forwarded-For": ip}
    if role:
        h.update({"X-Voyage-Gate": "g" * 32, "X-Voyage-Role": role, "X-Voyage-Name": "Rina%20Sorter"})
    if admin:
        h["X-Voyage-Administrator"] = "1"
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    try:
        with urllib.request.urlopen(r) as x:
            raw = x.read()
            return x.status, (json.loads(raw) if x.headers.get("Content-Type", "").startswith("application/json") else raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode()


def state(role="supervisor"):
    return req("GET", "/api/state", role=role)[1]


def tik(no):
    return next(t for t in state()["tickets"] if t["no"] == no)


# --- publik ---
s, r = req("GET", "/antri/instant")
check(s == 200 and "driver.js" in r, "halaman driver publik (tanpa gate)")
s, _ = req("GET", "/")
check(s == 403, "halaman staf tanpa gate -> 403")
for path, kode in (("/antri", 403), ("/antri/lain", 403), ("/sorter", 403), ("/static/staff.js", 403), ("/static/staff.html", 403),
                   ("/static/vendor/qrcode.js", 403), ("/static/driver.js", 200), ("/static/app.css", 200), ("/static/assets/vidi-ok.png", 200),
                   ("/static/../server.py", 403)):
    s, _ = req("GET", path)
    check(s == kode, "publik terkunci: %s -> %d" % (path, kode), s)
s, r = req("GET", "/static/staff.js", role="sorter")
check(s == 200, "berkas konsol staf terbuka lewat gate", s)
s, r = req("GET", "/antri/instant")
check("Pindah ke" not in r and "href=" not in r.split("<main")[1], "halaman driver tanpa tautan ke halaman lain")
s, _ = req("GET", "/api/state")
check(s == 403, "API staf tanpa gate -> 403")
s, r = req("POST", "/api/publik/tiket", {"layanan": "instant", "nama": "Budi", "hp": "0812-3456-6789", "kode": ["941g", "2wd4", "941G", "x"]})
check(s == 200 and r["no"].startswith("I-") and len(r["token"]) >= 16, "driver membuat tiket Instant", r)
tok, no = r["token"], r["no"]
s, p = req("GET", "/api/publik/tiket?t=" + tok)
check(s == 200 and [b["kode"] for b in p["bookings"]] == ["941G", "2WD4"] and p["posisi"] == 1 and "••••" in p["hp"]
      and "resi" not in json.dumps(p["bookings"]), "status publik: booking unik, posisi, HP disamarkan, tanpa resi", p)
s, r = req("POST", "/api/publik/tiket", {"layanan": "instant", "nama": "Orang Lain", "hp": "081234566789", "kode": ["AAAA"]})
check(s == 409, "HP yang sama dengan nama lain ditolak (tidak membocorkan tiket)", r)
s, r = req("POST", "/api/publik/tiket", {"layanan": "instant", "nama": "budi", "hp": "081234566789", "kode": ["AAAA"]})
check(s == 200 and r.get("ada") and r["token"] == tok, "driver yang sama membuka ulang tiket aktifnya", r)
s, _ = req("GET", "/api/publik/tiket?t=salah-token-yang-panjang")
check(s == 404, "token salah -> 404")
s, r = req("POST", "/api/publik/tiket", {"layanan": "sameday", "nama": "Sari", "hp": "081355512340", "kode": ["7KQ1"]})
check(s == 200 and r["no"] == "S-0001", "nomor Sameday terpisah (S-0001)", r)
for k in range(7):                  # batas 6 tiket / 10 menit per IP -> percobaan ke-7 ditolak
    s, r = req("POST", "/api/publik/tiket", {"layanan": "instant", "nama": "Spam%d" % k, "hp": "08990000%04d" % k, "kode": ["ZZZZ"]}, ip="9.9.9.9")
check(s == 429, "batas tiket per perangkat (rate limit)", r)

# --- peran ---
st = state("viewer")
check(st["me"]["peran"] == [] , "viewer tanpa peran", st["me"])
s, r = req("POST", "/api/aksi", {"aksi": "ambil", "id": tik(no)["id"]}, role="viewer")
check(s == 403, "viewer tidak bisa mengambil tiket", r)
check(state("member")["me"]["peran"] == ["loket", "sorter"], "member = sorter + loket")
check(state("sorter")["me"]["nama"] == "Rina Sorter", "nama staf dari header (percent-encoded)")
s, r = req("POST", "/api/aksi", {"aksi": "batas", "menit": 20}, role="sorter")
check(s == 403, "sorter tidak bisa mengubah pengaturan supervisor", r)

# --- alur sorter -> loket ---
tid = tik(no)["id"]
check(req("POST", "/api/aksi", {"aksi": "ambil", "id": tid}, role="sorter")[0] == 200, "sorter mengambil tiket")
s, r = req("POST", "/api/aksi", {"aksi": "ambil", "id": tid}, role="sorter")
check(s == 409, "tiket yang sudah diambil tidak bisa diambil lagi", r)
check(req("GET", "/api/publik/tiket?t=" + tok)[1]["status"] == "disiapkan", "driver melihat 'disiapkan'")
s, r = req("POST", "/api/aksi", {"aksi": "scan", "id": tid, "i": 0, "resi": "123"}, role="sorter")
check(s == 400, "resi terlalu pendek ditolak", r)
check(req("POST", "/api/aksi", {"aksi": "scan", "id": tid, "i": 0, "resi": "3342525163948544468"}, role="sorter")[0] == 200, "pindai resi booking 1")
s, r = req("POST", "/api/aksi", {"aksi": "scan", "id": tid, "i": 1, "resi": "3342525163948544468"}, role="sorter")
check(s == 409, "resi yang sama untuk booking lain ditolak", r)
s, r = req("POST", "/api/aksi", {"aksi": "siap", "id": tid}, role="sorter")
check(s == 409, "belum bisa siap sebelum semua dipindai", r)
check(req("POST", "/api/aksi", {"aksi": "scan", "id": tid, "i": 1, "resi": "3342525108143616468"}, role="sorter")[0] == 200, "pindai resi booking 2")
check(req("POST", "/api/aksi", {"aksi": "siap", "id": tid}, role="sorter")[0] == 200, "tandai siap")
t = tik(no)
check(t["status"] == "siap" and t["loket"] == 1, "siap -> Loket 1 (loket Instant yang buka)", t)
p = req("GET", "/api/publik/tiket?t=" + tok)[1]
check(p["status"] == "siap" and p["loket"] == 1 and 25 <= p["sisaMenit"] <= 30, "driver melihat loket & sisa waktu", p)
s, r = req("POST", "/api/aksi", {"aksi": "serah", "id": tid, "loket": 2}, role="loket")
check(s == 409, "serah di loket yang salah ditolak", r)
check(req("POST", "/api/aksi", {"aksi": "serah", "id": tid, "loket": 1}, role="loket")[0] == 200, "loket menyerahkan paket")
check(req("GET", "/api/publik/tiket?t=" + tok)[1]["status"] == "selesai", "driver melihat 'selesai'")

# --- bermasalah -> supervisor ---
sno = "S-0001"
sid = tik(sno)["id"]
req("POST", "/api/aksi", {"aksi": "ambil", "id": sid}, role="sorter")
check(req("POST", "/api/aksi", {"aksi": "tidak_ditemukan", "id": sid, "i": 0}, role="sorter")[0] == 200, "booking tidak ditemukan")
check(tik(sno)["status"] == "bermasalah", "tiket jadi bermasalah")
check(req("POST", "/api/aksi", {"aksi": "cari_ulang", "id": sid, "i": 0}, role="supervisor")[0] == 200 and tik(sno)["status"] == "disiapkan",
      "supervisor: cari ulang -> kembali disiapkan")
req("POST", "/api/aksi", {"aksi": "tidak_ditemukan", "id": sid, "i": 0}, role="sorter")
req("POST", "/api/aksi", {"aksi": "keluarkan", "id": sid, "i": 0}, role="supervisor")
t = tik(sno)
check(t["status"] == "dibatalkan" and t["cancel_reason"] == "semua booking dikeluarkan", "semua booking dikeluarkan -> dibatalkan", t)

# --- loket & kedaluwarsa ---
s, r = req("POST", "/api/publik/tiket", {"layanan": "sameday", "nama": "Joko", "hp": "089912345577", "kode": ["6WEZ"]}, ip="10.0.0.5")
jid = tik(r["no"])["id"]
req("POST", "/api/aksi", {"aksi": "ambil", "id": jid}, role="sorter")
req("POST", "/api/aksi", {"aksi": "scan", "id": jid, "i": 0, "resi": "3342525190012345468"}, role="sorter")
req("POST", "/api/aksi", {"aksi": "siap", "id": jid}, role="sorter")
check(tik(r["no"])["loket"] == 2, "Sameday -> Loket 2")
check(req("POST", "/api/aksi", {"aksi": "loket_buka", "i": 1}, role="supervisor")[0] == 200, "supervisor menutup Loket 2")
check(tik(r["no"])["loket"] is None, "tiket di loket tertutup kehilangan loket (tidak ada loket Sameday lain)", tik(r["no"]))
check(req("POST", "/api/aksi", {"aksi": "loket_layanan", "i": 2, "layanan": "sameday"}, role="supervisor")[0] == 200, "Loket 3 diubah ke Sameday")
with server.tx() as c:          # paksa lewat batas ambil
    c.execute("UPDATE tiket SET ready_at=? WHERE id=?", (server.now_ms() - 31 * 60000, jid))
t = tik(r["no"])
check(t["status"] == "dibatalkan" and t["cancel_reason"] == "lewat batas ambil", "lewat batas ambil -> dibatalkan otomatis", t)
check(req("POST", "/api/aksi", {"aksi": "kembali_rak", "id": jid}, role="supervisor")[0] == 200 and tik(r["no"])["returned"] == 1,
      "supervisor menandai paket dikembalikan ke rak")
check(req("POST", "/api/aksi", {"aksi": "batas", "menit": 45}, role="viewer", admin=True)[0] == 200 and state()["config"]["batasMenit"] == 45,
      "Administrator Voyage = supervisor")

srv.shutdown()
print(("MERAH ❌ %d gagal" % len(GAGAL)) if GAGAL else "HIJAU ✅ test_qhouse")
sys.exit(1 if GAGAL else 0)
