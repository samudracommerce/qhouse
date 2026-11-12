"""Q-House — antrian pengambilan paket SPX Instant & Sameday di gudang (Sheva 12 Nov 2026).

Bagian dari Wareflow (app gudang). Dibangun dari prototipe artifact "Antrian Gudang Instant".
Python standar saja (tanpa dependensi), data di SQLite (/data/qhouse.db).

Peran:
  - Driver (orang luar, TANPA akun Voyage): scan QR di pintu gudang -> /antri/instant | /antri/sameday, isi nama, HP,
    nomor booking 4 karakter -> tiket. Halaman & API publik (/antri*, /api/publik/*) — data minimal, dibatasi per IP.
  - Staf (lewat Caddy + voyage-gate; wajib X-Voyage-Gate = GATE_SECRET): Sorter, Loket, Supervisor, Barcode.
    Peran dari X-Voyage-Role (Voyage › Akses App › Q-House): admin/supervisor = semua; sorter; loket;
    member = sorter + loket; viewer = lihat saja.

Alur tiket: menunggu -> disiapkan (diambil sorter) -> siap (semua resi dipindai, dapat loket) -> selesai (serah terima loket).
Cabang: bermasalah (booking tidak ditemukan; supervisor: cari ulang / keluarkan / batalkan), dibatalkan
(supervisor, atau otomatis lewat batas ambil -> paket dikembalikan ke rak).
"""
import contextlib, hmac, json, mimetypes, os, re, secrets, sqlite3, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA = os.environ.get("QH_DATA", "/data")
DB_PATH = os.path.join(DATA, "qhouse.db")
GATE_SECRET = os.environ.get("GATE_SECRET", "")
NEED_GATE = os.environ.get("QH_REQUIRE_GATE", "1") == "1"
PUBLIC_URL = os.environ.get("QH_PUBLIC_URL", "http://localhost:8113").rstrip("/")
VOYAGE_URL = os.environ.get("QH_VOYAGE_URL", "").rstrip("/")
WIB = 7 * 3600

LAYANAN = {"instant": {"label": "SPX Instant", "prefix": "I"}, "sameday": {"label": "SPX Sameday", "prefix": "S"}}
AKTIF = ("menunggu", "disiapkan", "siap", "bermasalah")
CONFIG_AWAL = {"lokets": [{"n": 1, "layanan": "instant", "open": True}, {"n": 2, "layanan": "sameday", "open": True},
                          {"n": 3, "layanan": "instant", "open": False}],
               "batasMenit": 30, "rr": {"instant": 0, "sameday": 0}}
PERAN = {"admin": {"sorter", "loket", "supervisor"}, "supervisor": {"sorter", "loket", "supervisor"},
         "sorter": {"sorter"}, "loket": {"loket"}, "member": {"sorter", "loket"}, "viewer": set()}

LOCK = threading.Lock()


def now_ms():
    return int(time.time() * 1000)


def tgl_wib(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000 + WIB))


def hhmm(ms):
    return time.strftime("%H.%M", time.gmtime(ms / 1000 + WIB)) if ms else "–"


def norm_hp(s):
    return re.sub(r"\D", "", str(s or ""))[:16]


def norm_kode(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())[:4]


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


@contextlib.contextmanager
def tx():
    """Satu transaksi di bawah LOCK (penulis tunggal): commit bila sukses, rollback bila gagal, koneksi selalu ditutup."""
    with LOCK:
        c = db()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def init():
    os.makedirs(DATA, exist_ok=True)
    with tx() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tiket (
          id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, no TEXT NOT NULL, tgl TEXT NOT NULL, layanan TEXT NOT NULL,
          nama TEXT NOT NULL, hp TEXT NOT NULL, plat TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
          loket INTEGER, sorter TEXT, created_at INTEGER NOT NULL, taken_at INTEGER, ready_at INTEGER, done_at INTEGER,
          cancel_reason TEXT, returned INTEGER NOT NULL DEFAULT 0, bookings TEXT NOT NULL, log TEXT NOT NULL DEFAULT '[]');
        CREATE INDEX IF NOT EXISTS idx_tiket_status ON tiket(status);
        CREATE INDEX IF NOT EXISTS idx_tiket_tgl ON tiket(tgl);
        CREATE TABLE IF NOT EXISTS pengaturan (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if not c.execute("SELECT 1 FROM pengaturan WHERE key='config'").fetchone():
            c.execute("INSERT INTO pengaturan(key,value) VALUES('config',?)", (json.dumps(CONFIG_AWAL),))


# ---------- data ----------
def baris(r):
    t = dict(r)
    t["bookings"] = json.loads(t["bookings"])
    t["log"] = json.loads(t["log"])
    return t


def get_config(c):
    cfg = dict(CONFIG_AWAL)
    cfg.update(json.loads(c.execute("SELECT value FROM pengaturan WHERE key='config'").fetchone()[0]))
    return cfg


def set_config(c, cfg):
    c.execute("UPDATE pengaturan SET value=? WHERE key='config'", (json.dumps(cfg),))


def simpan(c, t):
    c.execute("""UPDATE tiket SET layanan=?, status=?, loket=?, sorter=?, taken_at=?, ready_at=?, done_at=?, cancel_reason=?,
                 returned=?, bookings=?, log=? WHERE id=?""",
              (t["layanan"], t["status"], t["loket"], t["sorter"], t["taken_at"], t["ready_at"], t["done_at"],
               t["cancel_reason"], int(bool(t["returned"])), json.dumps(t["bookings"]), json.dumps(t["log"][-40:]), t["id"]))


def catat(t, aksi, oleh=""):
    t["log"].append({"t": now_ms(), "a": aksi, "oleh": oleh})


def ambil(c, tid):
    r = c.execute("SELECT * FROM tiket WHERE id=?", (tid,)).fetchone()
    return baris(r) if r else None


def no_berikut(c, layanan, tgl):
    p = LAYANAN[layanan]["prefix"]
    mx = 0
    for (no,) in c.execute("SELECT no FROM tiket WHERE layanan=? AND tgl=?", (layanan, tgl)):
        try:
            mx = max(mx, int(no.split("-")[1]))
        except (IndexError, ValueError):
            pass
    return "%s-%04d" % (p, mx + 1)


def pilih_loket(layanan, cfg):
    buka = sorted(l["n"] for l in cfg["lokets"] if l["open"] and l["layanan"] == layanan)
    if not buka:
        return None
    rr = cfg.setdefault("rr", {})
    n = buka[rr.get(layanan, 0) % len(buka)]
    rr[layanan] = rr.get(layanan, 0) + 1
    return n


def kedaluwarsa(c):
    """Tiket 'siap' yang lewat batas ambil -> dibatalkan otomatis (paket harus dikembalikan ke rak)."""
    batas = int(get_config(c).get("batasMenit") or 30) * 60000
    for r in c.execute("SELECT * FROM tiket WHERE status='siap' AND ready_at IS NOT NULL AND ready_at < ?", (now_ms() - batas,)).fetchall():
        t = baris(r)
        t["status"], t["cancel_reason"], t["returned"] = "dibatalkan", "lewat batas ambil", 0
        catat(t, "Otomatis dibatalkan: lewat batas ambil")
        simpan(c, t)


def pindah_loket(c, n, cfg, oleh):
    for r in c.execute("SELECT * FROM tiket WHERE status='siap' AND loket=?", (n,)).fetchall():
        t = baris(r)
        t["loket"] = pilih_loket(t["layanan"], cfg)
        catat(t, ("Dipindah dari Loket %d ke Loket %d" % (n, t["loket"])) if t["loket"] else "Loket %d ditutup, menunggu loket baru" % n, oleh)
        simpan(c, t)


def antrian(c):
    return [baris(r) for r in c.execute("SELECT * FROM tiket WHERE status IN ('menunggu','disiapkan') ORDER BY created_at")]


# ---------- publik (driver) ----------
_RATE = {}


def rate_ok(ip, batas=6, jendela=600):
    t = time.time()
    xs = [x for x in _RATE.get(ip, []) if t - x < jendela]
    if len(xs) >= batas:
        _RATE[ip] = xs
        return False
    xs.append(t)
    _RATE[ip] = xs
    return True


def mask_hp(hp):
    d = norm_hp(hp)
    return d if len(d) < 6 else d[:4] + "••••" + d[-2:]


def tiket_publik(c, t):
    """Yang boleh dilihat driver lewat token: tanpa resi, HP disamarkan."""
    cfg = get_config(c)
    q = [x["id"] for x in antrian(c)]
    batas = int(cfg.get("batasMenit") or 30) * 60000
    return {"no": t["no"], "layanan": t["layanan"], "status": t["status"], "nama": t["nama"], "hp": mask_hp(t["hp"]),
            "bookings": [{"kode": b["kode"], "status": b["status"]} for b in t["bookings"]],
            "loket": t["loket"], "posisi": (q.index(t["id"]) + 1) if t["id"] in q else None, "antrian": len(q),
            "createdAt": t["created_at"], "readyAt": t["ready_at"], "doneAt": t["done_at"],
            "sisaMenit": max(0, round((t["ready_at"] + batas - now_ms()) / 60000)) if t["ready_at"] else None,
            "cancelReason": t["cancel_reason"]}


def buat_tiket(c, b, ip):
    lay = b.get("layanan")
    nama = str(b.get("nama") or "").strip()[:60]
    hp = norm_hp(b.get("hp"))
    plat = str(b.get("plat") or "").strip()[:16]
    kode = []
    for k in b.get("kode") or []:
        k = norm_kode(k)
        if len(k) == 4 and k not in kode:
            kode.append(k)
    if lay not in LAYANAN:
        return 400, {"error": "Pilih layanan Instant atau Sameday."}
    if not nama:
        return 400, {"error": "Isi nama dulu."}
    if len(hp) < 9:
        return 400, {"error": "Isi nomor HP yang aktif (minimal 9 angka)."}
    if not kode or len(kode) > 30:
        return 400, {"error": "Tambahkan 1–30 nomor booking (4 karakter)."}
    ada = c.execute("SELECT * FROM tiket WHERE hp=? AND status IN ('menunggu','disiapkan','siap','bermasalah') ORDER BY created_at DESC",
                    (hp,)).fetchone()
    if ada:
        if ada["nama"].strip().lower() == nama.lower():      # driver yang sama membuka ulang tiketnya
            return 200, {"token": ada["token"], "no": ada["no"], "ada": True}
        return 409, {"error": "Nomor HP ini sudah punya tiket aktif %s. Temui petugas gudang bila perlu bantuan." % ada["no"]}
    if not rate_ok(ip):
        return 429, {"error": "Terlalu banyak tiket dari perangkat ini. Coba lagi beberapa menit lagi."}
    t0 = now_ms()
    t = {"id": "t" + secrets.token_hex(6), "token": secrets.token_urlsafe(18), "tgl": tgl_wib(t0), "layanan": lay,
         "nama": nama, "hp": hp, "plat": plat, "status": "menunggu", "loket": None, "sorter": None, "created_at": t0,
         "taken_at": None, "ready_at": None, "done_at": None, "cancel_reason": None, "returned": 0,
         "bookings": [{"kode": k, "resi": None, "status": "menunggu"} for k in kode], "log": []}
    t["no"] = no_berikut(c, lay, t["tgl"])
    catat(t, "Tiket dibuat oleh driver")
    c.execute("""INSERT INTO tiket(id,token,no,tgl,layanan,nama,hp,plat,status,created_at,bookings,log)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (t["id"], t["token"], t["no"], t["tgl"], lay, nama, hp, plat, "menunggu", t0, json.dumps(t["bookings"]), json.dumps(t["log"])))
    return 200, {"token": t["token"], "no": t["no"]}


# ---------- staf ----------
def state(c, me):
    hari = tgl_wib(now_ms())
    rows = c.execute("SELECT * FROM tiket WHERE tgl=? OR status IN ('menunggu','disiapkan','siap','bermasalah')"
                     " OR (status='dibatalkan' AND ready_at IS NOT NULL AND returned=0) ORDER BY created_at", (hari,)).fetchall()
    return {"me": me, "config": get_config(c), "tickets": [baris(r) for r in rows], "now": now_ms(),
            "publicUrl": PUBLIC_URL, "voyageUrl": VOYAGE_URL, "layanan": LAYANAN}


def aksi(c, b, me):
    """Satu pintu aksi staf. -> (http, body). Izin per aksi dari peran me['peran']."""
    a = b.get("aksi")
    peran = set(me["peran"])
    oleh = me["nama"]
    perlu = {"ambil": "sorter", "scan": "sorter", "ulang": "sorter", "tidak_ditemukan": "sorter", "layanan": "sorter",
             "siap": "sorter", "serah": "loket", "loket_buka": "supervisor", "loket_layanan": "supervisor",
             "batas": "supervisor", "cari_ulang": "supervisor", "keluarkan": "supervisor", "batal": "supervisor",
             "kembali_rak": "supervisor"}.get(a)
    if not perlu:
        return 400, {"error": "Aksi tidak dikenal"}
    if perlu not in peran:
        return 403, {"error": "Akunmu tidak punya peran %s di Q-House." % perlu}
    cfg = get_config(c)
    if a in ("loket_buka", "loket_layanan", "batas"):
        if a == "batas":
            v = int(b.get("menit") or 0)
            if not 5 <= v <= 240:
                return 400, {"error": "Isi batas ambil antara 5 dan 240 menit."}
            cfg["batasMenit"] = v
            set_config(c, cfg)
            return 200, {"ok": True, "pesan": "Batas ambil diubah ke %d menit" % v}
        i = int(b.get("i", -1))
        if not 0 <= i < len(cfg["lokets"]):
            return 400, {"error": "Loket tidak dikenal"}
        L = cfg["lokets"][i]
        if a == "loket_buka":
            L["open"] = not L["open"]
            if not L["open"]:
                pindah_loket(c, L["n"], cfg, oleh)
            set_config(c, cfg)
            return 200, {"ok": True, "pesan": "Loket %d %s" % (L["n"], "dibuka" if L["open"] else "ditutup")}
        if b.get("layanan") not in LAYANAN:
            return 400, {"error": "Layanan tidak dikenal"}
        L["layanan"] = b["layanan"]
        set_config(c, cfg)
        return 200, {"ok": True, "pesan": "Loket %d melayani %s" % (L["n"], LAYANAN[L["layanan"]]["label"])}

    t = ambil(c, str(b.get("id") or ""))
    if not t:
        return 404, {"error": "Tiket tidak ditemukan"}
    bk = t["bookings"]
    i = b.get("i")
    i = int(i) if i is not None and str(i).lstrip("-").isdigit() else None
    if i is not None and not 0 <= i < len(bk):
        return 400, {"error": "Booking tidak dikenal"}
    if a == "ambil":
        if t["status"] != "menunggu":
            return 409, {"error": "Tiket ini sudah diambil sorter lain."}
        t["status"], t["sorter"], t["taken_at"] = "disiapkan", oleh, now_ms()
        catat(t, "Diambil sorter %s" % oleh, oleh)
    elif a == "scan":
        if t["status"] != "disiapkan" or i is None or bk[i]["status"] != "menunggu":
            return 409, {"error": "Booking ini tidak sedang menunggu pindai."}
        resi = re.sub(r"\s", "", str(b.get("resi") or ""))[:40]
        if len(resi) < 8:
            return 400, {"error": "Nomor resi terlalu pendek. Pindai ulang barcode di label."}
        for r in c.execute("SELECT no, bookings FROM tiket WHERE id<>? AND status IN ('menunggu','disiapkan','siap','bermasalah')", (t["id"],)):
            if any(x.get("resi") == resi for x in json.loads(r["bookings"])):
                return 409, {"error": "Resi ini sudah ada di tiket %s. Cek ulang paketnya." % r["no"]}
        if any(j != i and x.get("resi") == resi for j, x in enumerate(bk)):
            return 409, {"error": "Resi ini sudah dipindai untuk booking lain di tiket ini."}
        bk[i]["resi"], bk[i]["status"] = resi, "dipindai"
        catat(t, "Resi %s untuk %s" % (resi, bk[i]["kode"]), oleh)
    elif a == "ulang":
        if t["status"] != "disiapkan" or i is None or bk[i]["status"] != "dipindai":
            return 409, {"error": "Booking ini belum dipindai."}
        bk[i]["resi"], bk[i]["status"] = None, "menunggu"
        catat(t, "Resi %s diulang" % bk[i]["kode"], oleh)
    elif a == "tidak_ditemukan":
        if t["status"] != "disiapkan" or i is None or bk[i]["status"] != "menunggu":
            return 409, {"error": "Booking ini tidak sedang dicari."}
        bk[i]["status"], t["status"] = "tidak_ditemukan", "bermasalah"
        catat(t, "Booking %s tidak ditemukan" % bk[i]["kode"], oleh)
    elif a == "layanan":
        if t["status"] not in ("menunggu", "disiapkan", "bermasalah"):
            return 409, {"error": "Layanan hanya bisa dikoreksi sebelum tiket siap."}
        t["layanan"] = "sameday" if t["layanan"] == "instant" else "instant"
        catat(t, "Layanan dikoreksi ke %s" % LAYANAN[t["layanan"]]["label"], oleh)
    elif a == "siap":
        if t["status"] != "disiapkan":
            return 409, {"error": "Tiket belum disiapkan."}
        if not all(x["status"] in ("dipindai", "dikeluarkan") for x in bk) or not any(x["status"] == "dipindai" for x in bk):
            return 409, {"error": "Masih ada booking yang belum dipindai."}
        t["loket"] = pilih_loket(t["layanan"], cfg)
        set_config(c, cfg)
        t["status"], t["ready_at"] = "siap", now_ms()
        catat(t, ("Siap diambil di Loket %d" % t["loket"]) if t["loket"] else "Siap diambil, belum ada loket dibuka", oleh)
    elif a == "serah":
        n = int(b.get("loket") or 0)
        if t["status"] != "siap":
            return 409, {"error": "%s berstatus %s, belum bisa diserahkan." % (t["no"], t["status"])}
        if t["loket"] != n:
            return 409, {"error": "%s untuk Loket %s. Arahkan driver ke sana." % (t["no"], t["loket"] or "–")}
        t["status"], t["done_at"] = "selesai", now_ms()
        catat(t, "Diserahkan di Loket %d" % n, oleh)
    elif a in ("cari_ulang", "keluarkan"):
        if t["status"] != "bermasalah" or i is None or bk[i]["status"] != "tidak_ditemukan":
            return 409, {"error": "Booking ini tidak sedang bermasalah."}
        bk[i]["status"] = "menunggu" if a == "cari_ulang" else "dikeluarkan"
        catat(t, ("Booking %s dicari ulang" if a == "cari_ulang" else "Booking %s dikeluarkan") % bk[i]["kode"], oleh)
        if not any(x["status"] == "tidak_ditemukan" for x in bk):
            if all(x["status"] == "dikeluarkan" for x in bk):
                t["status"], t["cancel_reason"] = "dibatalkan", "semua booking dikeluarkan"
            else:
                t["status"] = "disiapkan" if t["taken_at"] else "menunggu"
    elif a == "batal":
        if t["status"] not in AKTIF:
            return 409, {"error": "Tiket ini sudah tidak aktif."}
        t["status"], t["cancel_reason"] = "dibatalkan", "dibatalkan supervisor"
        catat(t, "Dibatalkan supervisor", oleh)
    elif a == "kembali_rak":
        if t["status"] != "dibatalkan" or not t["ready_at"] or t["returned"]:
            return 409, {"error": "Tidak ada paket yang perlu dikembalikan untuk tiket ini."}
        t["returned"] = 1
        catat(t, "Paket dikembalikan ke rak", oleh)
    simpan(c, t)
    return 200, {"ok": True}


def me_dari(h):
    role = (h.get("X-Voyage-Role") or "viewer").strip().lower()
    peran = set(PERAN.get(role, set()))
    if h.get("X-Voyage-Administrator") == "1":
        peran = {"sorter", "loket", "supervisor"}
    return {"nama": urllib.parse.unquote(h.get("X-Voyage-Name") or "") or (h.get("X-Voyage-Email") or "Staf"),
            "email": h.get("X-Voyage-Email") or "", "role": role, "peran": sorted(peran)}


# ---------- HTTP ----------
# Publik (driver dari QR) HANYA halaman tiket per layanan + berkas yang dipakainya. Jalur lain (konsol staf, staff.js, dst.)
# wajib gate — Caddy membuka jalur yang sama persis (lihat Caddyfile :8113); server menegakkannya lagi.
PUBLIK_HALAMAN = re.compile(r"^/antri/(instant|sameday)/?$")
PUBLIK_STATIC = re.compile(r"^(driver\.js|app\.css|vendor/lucide\.min\.js|assets/[a-z0-9\-]+\.png)$")


class H(BaseHTTPRequestHandler):
    server_version = "QHouse/1"

    def log_message(self, *a):
        pass

    def out(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body) if not isinstance(body, str) else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def berkas(self, rel, cache=False):
        p = os.path.normpath(os.path.join(STATIC, rel))
        if not p.startswith(STATIC) or not os.path.isfile(p):
            return self.out(404, {"error": "tidak ada"})
        with open(p, "rb") as f:
            data = f.read()
        ct = mimetypes.guess_type(p)[0] or "application/octet-stream"
        if ct.startswith("text/") or ct.endswith("javascript"):
            ct += "; charset=utf-8"
        self.out(200, data, ct, {"Cache-Control": "public, max-age=3600"} if cache else None)

    def gated(self):
        if not NEED_GATE:
            return True
        tok = self.headers.get("X-Voyage-Gate") or ""
        if GATE_SECRET and tok and hmac.compare_digest(tok, GATE_SECRET):
            return True
        self.out(403, "Buka Q-House lewat Voyage.", "text/plain; charset=utf-8")
        return False

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            return None
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None

    def ip(self):
        # IP asli driver untuk batas tiket: Cloudflare (prod) mengisi CF-Connecting-IP; lokal = X-Forwarded-For dari Caddy
        return (self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        if p == "/healthz":
            return self.out(200, {"ok": True})
        if p.startswith("/static/"):
            rel = p[len("/static/"):]
            if not PUBLIK_STATIC.match(rel) and not self.gated():
                return
            return self.berkas(rel, cache=True)
        if PUBLIK_HALAMAN.match(p):
            return self.berkas("driver.html")
        if p == "/api/publik/tiket":
            tok = (urllib.parse.parse_qs(u.query).get("t") or [""])[0]
            with tx() as c:
                kedaluwarsa(c)
                r = c.execute("SELECT * FROM tiket WHERE token=?", (tok,)).fetchone() if len(tok) >= 16 else None
                return self.out(200, tiket_publik(c, baris(r))) if r else self.out(404, {"error": "Tiket tidak ditemukan"})
        if not self.gated():
            return
        if p in ("/", "/sorter", "/loket", "/supervisor", "/barcode"):
            return self.berkas("staff.html")
        if p == "/api/state":
            with tx() as c:
                kedaluwarsa(c)
                return self.out(200, state(c, me_dari(self.headers)))
        self.out(404, {"error": "tidak ada"})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        b = self.body()
        if b is None or not isinstance(b, dict):
            return self.out(400, {"error": "Data tidak valid"})
        if p == "/api/publik/tiket":
            with tx() as c:
                code, res = buat_tiket(c, b, self.ip())
            return self.out(code, res)
        if not self.gated():
            return
        if p == "/api/aksi":
            with tx() as c:
                kedaluwarsa(c)
                code, res = aksi(c, b, me_dari(self.headers))
            return self.out(code, res)
        self.out(404, {"error": "tidak ada"})


def main():
    if NEED_GATE and len(GATE_SECRET) < 24:
        raise SystemExit("GATE_SECRET wajib diisi (>= 24 karakter) — sama dengan voyage-gate")
    init()
    port = int(os.environ.get("PORT", "8000"))
    print("Q-House jalan di :%d (data %s)" % (port, DB_PATH), flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    main()
