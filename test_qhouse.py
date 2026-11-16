"""Tes Q-House alur v2 (python test_qhouse.py). Server sungguhan di port acak + DB sementara + Voyage tiruan.
Alur: driver -> dibagi otomatis ke sorter aktif -> Done -> loket pindai nomor pesanan -> serah terima -> validasi BigSeller.
Juga: gerbang staf, batas peran, sorter menghilang (tiket kembali ke antrean), kedaluwarsa, data publik yang disamarkan."""
import json, os, sys, tempfile, threading, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------- Voyage tiruan: /api/v1/bigseller/cek-pesanan + /api/v1/hooks/fleet/kabari ----------
VOY = {"cek": [], "kabari": [], "bs": {}}


class Voyage(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def kirim(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def kunci_ok(self):
        return self.headers.get("X-Api-Key") == "kunci-voyage" and self.headers.get("X-Voyage-App") == "qhouse"

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/v1/bigseller/cek-pesanan" or not self.kunci_ok():
            return self.kirim(403, {"error": "ditolak"})
        nomor = (urllib.parse.parse_qs(u.query).get("no") or [""])[0].split(",")
        VOY["cek"].append(nomor)
        kosong = {"ada": False, "status": "", "kode": "", "diserahkan": False, "kurir": ""}
        self.kirim(200, {"akun": "x", "hasil": {n: VOY["bs"].get(n, kosong) for n in nomor}})

    def do_POST(self):
        if self.path != "/api/v1/hooks/fleet/kabari" or not self.kunci_ok():
            return self.kirim(403, {"error": "ditolak"})
        VOY["kabari"].append(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0))))
        self.kirim(200, {"ok": True})


vsrv = ThreadingHTTPServer(("127.0.0.1", 0), Voyage)
threading.Thread(target=vsrv.serve_forever, daemon=True).start()

tmp = tempfile.mkdtemp()
os.environ.update(QH_DATA=tmp, GATE_SECRET="g" * 32, QH_REQUIRE_GATE="1", QH_PUBLIC_URL="http://qh.test",
                  QH_VOYAGE_INTERNAL="http://127.0.0.1:%d" % vsrv.server_address[1], QH_VOYAGE_KEY="kunci-voyage")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

server.init()
srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]
GAGAL = []

# staf: kunci -> (role Voyage, email, nama)
STAF = {"sup": ("supervisor", "sup@sri.test", "Sinta Supervisor"), "rina": ("sorter", "rina@sri.test", "Rina Sorter"),
        "dewi": ("member", "dewi@sri.test", "Dewi Gudang"), "loket": ("loket", "loket@sri.test", "Lukas Loket"),
        "viewer": ("viewer", "viewer@sri.test", "Vina Viewer")}


def check(ok, msg, info=""):
    print(("  [ok] " if ok else "  [GAGAL] ") + msg + ("" if ok else " :: " + str(info)))
    if not ok:
        GAGAL.append(msg)


def req(method, path, body=None, siapa=None, admin=False, ip="10.0.0.1"):
    h = {"Content-Type": "application/json", "X-Forwarded-For": ip}
    if siapa:
        role, email, nama = STAF[siapa]
        h.update({"X-Voyage-Gate": "g" * 32, "X-Voyage-Role": role, "X-Voyage-Email": email,
                  "X-Voyage-Name": urllib.parse.quote(nama)})
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


def state(siapa="sup", tab=""):
    return req("GET", "/api/state?tab=" + tab, siapa=siapa)[1]


def aksi(siapa, **b):
    return req("POST", "/api/aksi", b, siapa=siapa)


def tik(no):
    return next(t for t in state()["tickets"] if t["no"] == no)


def publik(tok):
    return req("GET", "/api/publik/tiket?t=" + tok)[1]


def driver(layanan, nama, hp, kode, ip="10.0.0.1"):
    return req("POST", "/api/publik/tiket", {"layanan": layanan, "nama": nama, "hp": hp, "kode": kode}, ip=ip)


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
s, r = req("GET", "/static/staff.js", siapa="rina")
check(s == 200, "berkas konsol staf terbuka lewat gate", s)
s, r = req("GET", "/antri/instant")
check("Pindah ke" not in r and "href=" not in r.split("<main")[1], "halaman driver tanpa tautan ke halaman lain")
s, _ = req("GET", "/api/state")
check(s == 403, "API staf tanpa gate -> 403")
s, r = driver("instant", "Budi", "0812-3456-6789", ["941g", "2wd4", "941G", "x"])
check(s == 200 and r["no"] == "I-0001" and len(r["token"]) >= 16, "driver membuat tiket Instant", r)
tok_budi, no_budi = r["token"], r["no"]
p = publik(tok_budi)
check([b["kode"] for b in p["bookings"]] == ["941G", "2WD4"] and p["posisi"] == 1 and "••••" in p["hp"]
      and "resi" not in json.dumps(p["bookings"]) and "pesanan" not in p, "status publik: booking unik, posisi, HP disamarkan, tanpa nomor pesanan", p)
s, r = driver("instant", "Orang Lain", "081234566789", ["AAAA"])
check(s == 409, "HP yang sama dengan nama lain ditolak (tidak membocorkan tiket)", r)
s, r = driver("instant", "budi", "081234566789", ["AAAA"])
check(s == 200 and r.get("ada") and r["token"] == tok_budi, "driver yang sama membuka ulang tiket aktifnya", r)
s, _ = req("GET", "/api/publik/tiket?t=salah-token-yang-panjang")
check(s == 404, "token salah -> 404")
s, r = driver("sameday", "Sari", "081355512340", ["7KQ1"])
check(s == 200 and r["no"] == "S-0001", "nomor Sameday terpisah (S-0001)", r)
tok_sari, no_sari = r["token"], r["no"]

# --- peran ---
st = state("viewer")
check(st["me"]["peran"] == [], "viewer tanpa peran", st["me"])
check(state("dewi")["me"]["peran"] == ["loket", "sorter"], "member = sorter + loket")
check(state("rina")["me"]["nama"] == "Rina Sorter", "nama staf dari header (percent-encoded)")
s, r = aksi("viewer", aksi="done", id=tik(no_budi)["id"])
check(s == 403, "viewer tidak bisa Done", r)
s, r = aksi("rina", aksi="batas", menit=20)
check(s == 403, "sorter tidak bisa mengubah pengaturan supervisor", r)
s, r = aksi("loket", aksi="done", id=tik(no_budi)["id"])
check(s == 403, "loket tidak bisa Done", r)
s, r = aksi("sup", aksi="ambil", id=tik(no_budi)["id"])
check(s == 400, "aksi lama 'ambil' sudah tidak dikenal", r)

# --- pembagian otomatis ---
check(tik(no_budi)["status"] == "menunggu" and state()["sorterAktif"] == [], "tanpa sorter aktif -> tiket tetap menunggu")
st = state("rina", "sorter")                     # Rina membuka tab Sorter -> semua tiket menunggu dibagi ke Rina
check([s_["email"] for s_ in st["sorterAktif"]] == ["rina@sri.test"], "Rina tercatat sorter aktif", st["sorterAktif"])
t = tik(no_budi)
check(t["status"] == "disiapkan" and t["sorter_email"] == "rina@sri.test" and t["sorter"] == "Rina Sorter", "tiket Budi dibagi ke Rina", t)
check(tik(no_sari)["sorter_email"] == "rina@sri.test", "tiket Sari juga ke Rina (satu-satunya sorter)")
check(publik(tok_budi)["status"] == "disiapkan", "driver melihat 'disiapkan'")
state("dewi", "sorter")                          # Dewi ikut menyortir
s, r = driver("sameday", "Joko", "089912345577", ["6WEZ"], ip="10.0.0.5")
tok_joko, no_joko = r["token"], r["no"]
check(tik(no_joko)["sorter_email"] == "dewi@sri.test", "tiket baru ke sorter dengan beban paling sedikit (Dewi)", tik(no_joko))
state("loket", "sorter")
check("loket@sri.test" not in [x["email"] for x in state()["sorterAktif"]], "akun tanpa peran sorter tidak ikut dibagi")

# --- sorter menghilang / ditarik supervisor ---
with server.tx() as c:                           # Dewi tidak terlihat > 5 menit
    c.execute("UPDATE hadir SET last_seen=? WHERE email='dewi@sri.test'", (server.now_ms() - 6 * 60000,))
t = tik(no_joko)
check(t["sorter_email"] == "rina@sri.test" and any("Dikembalikan ke antrean" in x["a"] for x in t["log"]),
      "sorter menghilang -> tiket kembali ke antrean lalu dibagi ke sorter aktif", t)
check(aksi("sup", aksi="lepas", id=t["id"])[0] == 200 and any("Ditarik dari sorter" in x["a"] for x in tik(no_joko)["log"]),
      "supervisor menarik tiket dari sorter")
s, r = aksi("sup", aksi="lepas", id=tik(no_budi)["id"])
check(s == 200 and tik(no_budi)["status"] == "disiapkan" and tik(no_budi)["sorter_email"] == "rina@sri.test",
      "tiket yang ditarik langsung dibagi ulang ke sorter aktif", tik(no_budi))

# --- Done -> loket ---
state("dewi", "sorter")                          # Dewi kembali aktif, tapi tiket Budi milik Rina
s, r = aksi("dewi", aksi="done", id=tik(no_budi)["id"])
check(s == 403, "sorter lain tidak bisa Done tiket yang bukan miliknya", r)
check(aksi("rina", aksi="done", id=tik(no_budi)["id"])[0] == 200, "sorter pemilik menekan Done")
t = tik(no_budi)
check(t["status"] == "siap" and t["loket"] == 1, "Done -> siap di Loket 1 (loket Instant yang buka)", t)
s, r = aksi("rina", aksi="done", id=t["id"])
check(s == 409, "Done dua kali ditolak", r)
p = publik(tok_budi)
check(p["status"] == "siap" and p["loket"] == 1 and 25 <= p["sisaMenit"] <= 30, "driver melihat loket & sisa waktu", p)

# --- loket memindai nomor pesanan ---
bid = t["id"]
s, r = aksi("loket", aksi="serah", id=bid, loket=1)
check(s == 409 and "0 dari 2" in r["error"], "belum bisa serah sebelum nomor pesanan dipindai", r)
s, r = aksi("loket", aksi="scan", id=bid, no="QH1")
check(s == 400, "nomor pesanan terlalu pendek ditolak", r)
check(aksi("loket", aksi="scan", id=bid, no="251112abc-def01")[0] == 200 and tik(no_budi)["pesanan"] == ["251112ABCDEF01"],
      "pindai nomor pesanan 1 (dinormalisasi huruf besar, tanpa tanda baca)")
s, r = aksi("loket", aksi="scan", id=bid, no="251112ABCDEF01")
check(s == 409, "nomor pesanan yang sama dua kali ditolak", r)
check(aksi("loket", aksi="scan", id=bid, no="251112SALAH999")[0] == 200, "pindai nomor pesanan 2 (salah)")
s, r = aksi("loket", aksi="scan", id=bid, no="251112LEBIH777")
check(s == 409, "lebih dari jumlah booking ditolak", r)
check(aksi("loket", aksi="hapus_scan", id=bid, i=1)[0] == 200 and tik(no_budi)["pesanan"] == ["251112ABCDEF01"], "hapus pindaian yang salah")
check(aksi("loket", aksi="scan", id=bid, no="251112GHIJKL02")[0] == 200, "pindai ulang nomor pesanan 2")
s, r = aksi("loket", aksi="serah", id=bid, loket=2)
check(s == 409, "serah di loket yang salah ditolak", r)
s, r = aksi("rina", aksi="scan", id=bid, no="251112XYZ00003")
check(s == 403, "sorter murni tidak bisa memindai di loket", r)
check(aksi("loket", aksi="serah", id=bid, loket=1)[0] == 200, "loket menyerahkan paket")
t = tik(no_budi)
check(t["status"] == "selesai" and t["done_by"] == "Lukas Loket", "tiket selesai + tercatat petugas loket", t)
check(publik(tok_budi)["status"] == "selesai", "driver melihat 'selesai'")

# --- nomor pesanan dipakai tiket lain; loket ditutup; kedaluwarsa ---
sari = tik(no_sari)
aksi(sari["sorter_email"].split("@")[0], aksi="done", id=sari["id"])
check(tik(no_sari)["loket"] == 2, "Sameday -> Loket 2")
s, r = aksi("loket", aksi="scan", id=sari["id"], no="251112ABCDEF01")
check(s == 409 and no_budi in r["error"], "nomor pesanan yang sudah ada di tiket lain ditolak", r)
check(aksi("sup", aksi="loket_buka", i=1)[0] == 200, "supervisor menutup Loket 2")
check(tik(no_sari)["loket"] is None, "tiket di loket tertutup kehilangan loket (tidak ada loket Sameday lain)", tik(no_sari))
check(aksi("sup", aksi="loket_layanan", i=2, layanan="sameday")[0] == 200, "Loket 3 diubah ke Sameday")
with server.tx() as c:                           # paksa lewat batas ambil
    c.execute("UPDATE tiket SET ready_at=? WHERE id=?", (server.now_ms() - 31 * 60000, sari["id"]))
t = tik(no_sari)
check(t["status"] == "dibatalkan" and t["cancel_reason"] == "lewat batas ambil", "lewat batas ambil -> dibatalkan otomatis", t)
check(aksi("sup", aksi="kembali_rak", id=sari["id"])[0] == 200 and tik(no_sari)["returned"] == 1, "supervisor menandai paket dikembalikan ke rak")
check(aksi("sup", aksi="batal", id=tik(no_joko)["id"])[0] == 200 and tik(no_joko)["status"] == "dibatalkan", "supervisor membatalkan tiket")

# --- validasi BigSeller ---
hari = server.tgl_wib(server.now_ms())
s, r = aksi("loket", aksi="validasi", tgl=hari)
check(s == 403, "validasi hanya supervisor", r)
s, r = aksi("sup", aksi="validasi", tgl="kemarin")
check(s == 400, "tanggal validasi tidak valid ditolak", r)
VOY["bs"] = {"251112ABCDEF01": {"ada": True, "status": "Dikirim", "kode": "shipped", "diserahkan": True, "kurir": "SPX Instant"},
             "251112GHIJKL02": {"ada": True, "status": "Siap dikirim", "kode": "readytoship", "diserahkan": False, "kurir": "SPX Instant"}}
check(server.jalankan_validasi(hari, "tes") is True, "validasi berjalan")
check(VOY["cek"] and sorted(VOY["cek"][-1]) == ["251112ABCDEF01", "251112GHIJKL02"], "Voyage ditanya nomor pesanan yang diserahkan", VOY["cek"])
v = state()["validasi"]
lg = v["log"][0]
check(lg["tgl"] == hari and lg["total"] == 2 and lg["cocok"] == 1 and lg["masalah"] == 1 and not lg["error"], "ringkasan validasi", lg)
check(len(v["masalah"]) == 1 and v["masalah"][0]["no_pesanan"] == "251112GHIJKL02" and v["masalah"][0]["hasil"] == "belum_diserahkan"
      and v["masalah"][0]["tiket_no"] == no_budi, "pesanan yang belum diserahkan ke kurir tampil di Supervisor", v["masalah"])
check(VOY["kabari"] and "sup@sri.test" in VOY["kabari"][-1]["to"] and "rina@sri.test" not in VOY["kabari"][-1]["to"]
      and VOY["kabari"][-1]["url"].endswith("/supervisor"), "lonceng Voyage hanya ke supervisor", VOY["kabari"])
check(server.hasil_dari({"ada": False}) == "tidak_ditemukan" and server.hasil_dari({"ada": True, "kode": "cancelled"}) == "batal",
      "hasil: tidak ditemukan / batal")
VOY["bs"]["251112GHIJKL02"]["diserahkan"] = True
n_kabari = len(VOY["kabari"])
server.jalankan_validasi(hari, "tes")
v = state()["validasi"]
check(v["masalah"] == [] and v["log"][0]["cocok"] == 2 and len(VOY["kabari"]) == n_kabari, "validasi ulang menimpa hasil; tanpa masalah = tanpa lonceng", v)
asli = server.VOYAGE_KEY
server.VOYAGE_KEY = "kunci-salah"
server.jalankan_validasi(hari, "tes")
server.VOYAGE_KEY = asli
check("403" in state()["validasi"]["log"][0]["error"], "Voyage menolak -> galat tercatat (dicoba lagi oleh penjadwal)", state()["validasi"]["log"][0])

# --- lain-lain ---
check(aksi("viewer", aksi="batas", menit=45)[0] == 403, "viewer tidak bisa mengubah batas")
check(req("POST", "/api/aksi", {"aksi": "batas", "menit": 45}, siapa="viewer", admin=True)[0] == 200 and state()["config"]["batasMenit"] == 45,
      "Administrator Voyage = supervisor")
for k in range(7):                  # batas 6 tiket / 10 menit per IP -> percobaan ke-7 ditolak
    s, r = driver("instant", "Spam%d" % k, "08990000%04d" % k, ["ZZZZ"], ip="9.9.9.9")
check(s == 429, "batas tiket per perangkat (rate limit)", r)

# --- Q-House sebagai fitur Wareflow di PATH /qhouse (Sheva 16 Nov 2026) ---
print("— prefix QH_BASE=/qhouse —")
check(server.BASE == "", "bawaan tanpa prefix (perilaku lama)")
s, h = req("GET", "/antri/instant")
check(s == 200 and '<meta name="qh-base" content="">' in h and '"/static/driver.js"' in h, "tanpa prefix: halaman driver tetap di akar")
server.BASE = "/qhouse"
check(server.jalur("/qhouse/antri/instant") == "/antri/instant" and server.jalur("/qhouse") == "/" and server.jalur("/qhouse/") == "/"
      and server.jalur("/qhousex/api") == "/qhousex/api" and server.jalur("/antri/sameday") == "/antri/sameday",
      "jalur: prefix dipotong hanya bila tepat /qhouse atau /qhouse/…")
s, h = req("GET", "/qhouse/antri/instant")
check(s == 200 and '<meta name="qh-base" content="/qhouse">' in h and '"/qhouse/static/driver.js"' in h
      and '"/qhouse/static/app.css"' in h and '"/static/' not in h, "halaman driver di /qhouse: semua berkas statis ber-prefix")
check(req("GET", "/qhouse/static/driver.js")[0] == 200 and req("GET", "/qhouse/static/staff.js")[0] == 403,
      "statis publik tetap publik; staff.js tetap wajib gate (lewat prefix)")
s, r = req("POST", "/qhouse/api/publik/tiket", {"layanan": "sameday", "nama": "Driver Prefix", "hp": "081299990000", "kode": ["PREF1"]}, ip="7.7.7.7")
check(s == 200 and r.get("token") and req("GET", "/qhouse/api/publik/tiket?t=" + r["token"])[0] == 200, "tiket driver lewat /qhouse/api/publik/tiket", r)
check(req("GET", "/qhouse")[0] == 403 and req("GET", "/qhouse/sorter")[0] == 403, "konsol staf lewat prefix tetap wajib gate")
s, h = req("GET", "/qhouse/supervisor", siapa="sup")
check(s == 200 and '<meta name="qh-base" content="/qhouse">' in h and '"/qhouse/static/staff.js"' in h, "konsol staf di /qhouse/supervisor")
check(req("GET", "/qhouse/api/state?tab=supervisor", siapa="sup")[0] == 200 and req("POST", "/qhouse/api/aksi", {"aksi": "batas", "menit": 30}, siapa="sup")[0] == 200,
      "API staf lewat prefix")
server.BASE = ""

srv.shutdown()
vsrv.shutdown()
print(("MERAH ❌ %d gagal" % len(GAGAL)) if GAGAL else "HIJAU ✅ test_qhouse")
sys.exit(1 if GAGAL else 0)
