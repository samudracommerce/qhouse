"""Q-House — antrian pengambilan paket SPX Instant & Sameday di gudang (Sheva 12 Nov 2026). Fitur Wareflow (app bs-acc).

Python standar saja (tanpa dependensi), data di SQLite (/data/qhouse.db).

Peran:
  - Driver (orang luar, TANPA akun Voyage): scan QR di pintu gudang -> /antri/instant | /antri/sameday, isi nama, HP,
    nomor booking 4 karakter -> tiket. Halaman & API publik — data minimal, dibatasi per IP, satu tiket aktif per HP.
  - Staf (lewat Caddy + voyage-gate fitur bs-acc/qhouse; wajib X-Voyage-Gate = GATE_SECRET): Sorter, Loket, Supervisor,
    Barcode. Peran dari X-Voyage-Role (Voyage › Akses App › Wareflow): admin/supervisor = semua; sorter; loket;
    member = sorter + loket; viewer = lihat saja.

Alur (Sheva 12 Nov 2026, versi 2):
  menunggu -> disiapkan  : DIBAGI OTOMATIS ke sorter yang sedang membuka tab Sorter (beban paling sedikit). Sorter yang
                           tabnya tertutup / sesi Voyage habis berhenti menerima; tiketnya kembali ke antrean setelah 5 menit.
  disiapkan -> siap      : sorter menekan Done -> dapat loket (bergiliran per layanan).
  siap -> selesai        : loket memindai NOMOR PESANAN marketplace tiap paket (jumlah = jumlah booking), lalu serah terima.
  Pukul 01.00 WIB        : nomor pesanan yang selesai kemarin divalidasi ke BigSeller lewat Voyage (/api/v1/bigseller/cek-pesanan):
                           cocok = ada & sudah diserahkan ke kurir. Yang tidak cocok tampil di tab Supervisor + lonceng Voyage.
  Cabang: dibatalkan (supervisor, atau otomatis lewat batas ambil -> paket dikembalikan ke rak).
"""
import contextlib, hmac, json, mimetypes, os, re, secrets, sqlite3, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA = os.environ.get("QH_DATA", "/data")
DB_PATH = os.path.join(DATA, "qhouse.db")
GATE_SECRET = os.environ.get("GATE_SECRET", "")
NEED_GATE = os.environ.get("QH_REQUIRE_GATE", "1") == "1"
PUBLIC_URL = os.environ.get("QH_PUBLIC_URL", "http://localhost:8113").rstrip("/")
# Q-House = fitur Wareflow di PATH (Sheva 16 Nov 2026): wareflow.samudracommerce.com/qhouse/… → QH_BASE="/qhouse".
# Kosong = di akar domain (perilaku lama). Server menerima jalur dengan ATAU tanpa prefix (Caddy boleh memotong/meneruskannya).
BASE = os.environ.get("QH_BASE", "").strip().rstrip("/")
if BASE and not re.fullmatch(r"/[a-z0-9\-]+", BASE):
    raise SystemExit("QH_BASE harus berbentuk /nama (huruf kecil, angka, -), mis. /qhouse")
VOYAGE_URL = os.environ.get("QH_VOYAGE_URL", "").rstrip("/")
VOYAGE_INTERNAL = os.environ.get("QH_VOYAGE_INTERNAL", "").rstrip("/")     # panggilan server-ke-server ke Voyage
VOYAGE_KEY = os.environ.get("QH_VOYAGE_KEY", "")                          # api_key Voyage (= kunci layanan fleet)
JAM_VALIDASI = int(os.environ.get("QH_JAM_VALIDASI", "1"))                # 01.00 WIB
WIB = 7 * 3600

LAYANAN = {"instant": {"label": "SPX Instant", "prefix": "I"}, "sameday": {"label": "SPX Sameday", "prefix": "S"}}
AKTIF = ("menunggu", "disiapkan", "siap", "bermasalah")
CONFIG_AWAL = {"lokets": [{"n": 1, "layanan": "instant", "open": True}, {"n": 2, "layanan": "sameday", "open": True},
                          {"n": 3, "layanan": "instant", "open": False}],
               "batasMenit": 30, "rr": {"instant": 0, "sameday": 0}}
PERAN = {"admin": {"sorter", "loket", "supervisor"}, "supervisor": {"sorter", "loket", "supervisor"},
         "sorter": {"sorter"}, "loket": {"loket"}, "member": {"sorter", "loket"}, "viewer": set()}
SORTER_AKTIF_DETIK = 120     # tab Sorter terbuka (polling; tab latar dibatasi browser ~1x/menit)
SORTER_LEPAS_DETIK = 300     # sorter tak terlihat selama ini -> tiketnya kembali ke antrean
NO_PESANAN = re.compile(r"^[A-Z0-9]{6,40}$")

LOCK = threading.Lock()


def now_ms():
    return int(time.time() * 1000)


def tgl_wib(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000 + WIB))


def norm_hp(s):
    return re.sub(r"\D", "", str(s or ""))[:16]


def norm_kode(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())[:4]


def norm_pesanan(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())[:40]


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
        CREATE TABLE IF NOT EXISTS hadir (email TEXT PRIMARY KEY, nama TEXT NOT NULL, role TEXT NOT NULL, peran TEXT NOT NULL,
          tab TEXT NOT NULL DEFAULT '', last_seen INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS validasi (no_pesanan TEXT PRIMARY KEY, tiket_id TEXT NOT NULL, tiket_no TEXT NOT NULL,
          tgl TEXT NOT NULL, hasil TEXT NOT NULL, status TEXT NOT NULL DEFAULT '', kurir TEXT NOT NULL DEFAULT '', cek_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_validasi_tgl ON validasi(tgl);
        """)
        kolom = {r[1] for r in c.execute("PRAGMA table_info(tiket)")}
        for k, d in (("sorter_email", "TEXT"), ("pesanan", "TEXT NOT NULL DEFAULT '[]'"), ("siap_by", "TEXT"), ("done_by", "TEXT")):
            if k not in kolom:
                c.execute("ALTER TABLE tiket ADD COLUMN %s %s" % (k, d))
        if not c.execute("SELECT 1 FROM pengaturan WHERE key='config'").fetchone():
            c.execute("INSERT INTO pengaturan(key,value) VALUES('config',?)", (json.dumps(CONFIG_AWAL),))


# ---------- data ----------
def baris(r):
    t = dict(r)
    t["bookings"] = json.loads(t["bookings"])
    t["log"] = json.loads(t["log"])
    t["pesanan"] = json.loads(t.get("pesanan") or "[]")
    return t


def get_config(c):
    cfg = dict(CONFIG_AWAL)
    cfg.update(json.loads(c.execute("SELECT value FROM pengaturan WHERE key='config'").fetchone()[0]))
    return cfg


def set_config(c, cfg):
    c.execute("UPDATE pengaturan SET value=? WHERE key='config'", (json.dumps(cfg),))


def get_set(c, key, awal):
    r = c.execute("SELECT value FROM pengaturan WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else awal


def put_set(c, key, val):
    c.execute("INSERT INTO pengaturan(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(val)))


def simpan(c, t):
    c.execute("""UPDATE tiket SET layanan=?, status=?, loket=?, sorter=?, sorter_email=?, taken_at=?, ready_at=?, done_at=?,
                 cancel_reason=?, returned=?, bookings=?, log=?, pesanan=?, siap_by=?, done_by=? WHERE id=?""",
              (t["layanan"], t["status"], t["loket"], t["sorter"], t.get("sorter_email"), t["taken_at"], t["ready_at"], t["done_at"],
               t["cancel_reason"], int(bool(t["returned"])), json.dumps(t["bookings"]), json.dumps(t["log"][-40:]),
               json.dumps(t.get("pesanan") or []), t.get("siap_by"), t.get("done_by"), t["id"]))


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


# ---------- sorter: kehadiran & pembagian otomatis ----------
def hadir_catat(c, me, tab):
    if not me["email"]:
        return
    c.execute("""INSERT INTO hadir(email,nama,role,peran,tab,last_seen) VALUES(?,?,?,?,?,?)
                 ON CONFLICT(email) DO UPDATE SET nama=excluded.nama, role=excluded.role, peran=excluded.peran, tab=excluded.tab,
                 last_seen=excluded.last_seen""", (me["email"], me["nama"], me["role"], json.dumps(me["peran"]), tab or "", now_ms()))


def sorter_aktif(c):
    batas = now_ms() - SORTER_AKTIF_DETIK * 1000
    return [dict(r) for r in c.execute("SELECT email, nama FROM hadir WHERE tab='sorter' AND last_seen >= ? AND peran LIKE '%\"sorter\"%'"
                                       " ORDER BY nama", (batas,))]


def bagikan(c):
    """Lepas tiket dari sorter yang menghilang, lalu bagi tiket 'menunggu' ke sorter aktif dengan beban paling sedikit."""
    t0 = now_ms()
    # dilepas bila sorternya tidak terlihat di tab mana pun (pindah tab Loket sebentar tidak melepas tiketnya)
    terlihat = {r["email"]: r["last_seen"] for r in c.execute("SELECT email, last_seen FROM hadir")}
    for r in c.execute("SELECT * FROM tiket WHERE status='disiapkan' AND sorter_email IS NOT NULL").fetchall():
        if terlihat.get(r["sorter_email"], 0) < t0 - SORTER_LEPAS_DETIK * 1000:
            t = baris(r)
            catat(t, "Dikembalikan ke antrean: sorter %s tidak aktif" % (t["sorter"] or ""))
            t["status"], t["sorter"], t["sorter_email"], t["taken_at"] = "menunggu", None, None, None
            simpan(c, t)
    aktif = sorter_aktif(c)
    if not aktif:
        return
    beban = {s["email"]: 0 for s in aktif}
    for (e, n) in c.execute("SELECT sorter_email, COUNT(*) FROM tiket WHERE status='disiapkan' GROUP BY sorter_email"):
        if e in beban:
            beban[e] = n
    nama = {s["email"]: s["nama"] for s in aktif}
    for r in c.execute("SELECT * FROM tiket WHERE status='menunggu' ORDER BY created_at").fetchall():
        e = min(beban, key=lambda x: (beban[x], nama[x]))
        t = baris(r)
        t["status"], t["sorter"], t["sorter_email"], t["taken_at"] = "disiapkan", nama[e], e, now_ms()
        catat(t, "Dibagikan otomatis ke sorter %s" % nama[e])
        simpan(c, t)
        beban[e] += 1


def rawat(c):
    kedaluwarsa(c)
    bagikan(c)


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
    """Yang boleh dilihat driver lewat token: tanpa nomor pesanan, HP disamarkan."""
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
        return 400, {"error": "Isi 1–30 nomor booking (4 karakter)."}
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
         "bookings": [{"kode": k, "resi": None, "status": "menunggu"} for k in kode], "log": []}
    t["no"] = no_berikut(c, lay, t["tgl"])
    catat(t, "Tiket dibuat oleh driver")
    c.execute("""INSERT INTO tiket(id,token,no,tgl,layanan,nama,hp,plat,status,created_at,bookings,log)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (t["id"], t["token"], t["no"], t["tgl"], lay, nama, hp, plat, "menunggu", t0, json.dumps(t["bookings"]), json.dumps(t["log"])))
    bagikan(c)
    return 200, {"token": t["token"], "no": t["no"]}


# ---------- validasi BigSeller (pukul 01.00) ----------
_VALIDASI_JALAN = threading.Event()


def _voyage(path, body=None, timeout=900):
    if not (VOYAGE_INTERNAL and VOYAGE_KEY):
        raise RuntimeError("QH_VOYAGE_INTERNAL / QH_VOYAGE_KEY belum diisi")
    h = {"X-Api-Key": VOYAGE_KEY, "X-Voyage-App": "qhouse"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(VOYAGE_INTERNAL + path, data=data, headers=h, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            pesan = json.loads(e.read()).get("error")
        except ValueError:
            pesan = None
        raise RuntimeError("Voyage %s: %s" % (e.code, pesan or e.reason))


def hasil_dari(v):
    if not v.get("ada"):
        return "tidak_ditemukan"
    if v.get("diserahkan"):
        return "cocok"
    return "batal" if "cancel" in (v.get("kode") or "") else "belum_diserahkan"


def jalankan_validasi(tgl, oleh="jadwal"):
    """Validasi nomor pesanan dari tiket yang SELESAI pada tanggal tgl (WIB). Aman dipanggil berulang (hasil ditimpa)."""
    if _VALIDASI_JALAN.is_set():
        return False
    _VALIDASI_JALAN.set()
    log = {"tgl": tgl, "mulai": now_ms(), "oleh": oleh, "total": 0, "cocok": 0, "masalah": 0, "error": ""}
    try:
        with tx() as c:
            daftar = []
            for r in c.execute("SELECT * FROM tiket WHERE status='selesai'").fetchall():
                t = baris(r)
                if t["done_at"] and tgl_wib(t["done_at"]) == tgl:
                    daftar += [(no, t["id"], t["no"]) for no in t["pesanan"]]
        log["total"] = len(daftar)
        hasil = {}
        for i in range(0, len(daftar), 50):
            nomor = [x[0] for x in daftar[i:i + 50]]
            hasil.update(_voyage("/api/v1/bigseller/cek-pesanan?no=" + urllib.parse.quote(",".join(nomor)))["hasil"])
        t1 = now_ms()
        with tx() as c:
            for no, tid, tno in daftar:
                v = hasil.get(no) or {"ada": False}
                h = hasil_dari(v)
                log["cocok" if h == "cocok" else "masalah"] += 1
                c.execute("""INSERT INTO validasi(no_pesanan,tiket_id,tiket_no,tgl,hasil,status,kurir,cek_at) VALUES(?,?,?,?,?,?,?,?)
                             ON CONFLICT(no_pesanan) DO UPDATE SET tiket_id=excluded.tiket_id, tiket_no=excluded.tiket_no, tgl=excluded.tgl,
                             hasil=excluded.hasil, status=excluded.status, kurir=excluded.kurir, cek_at=excluded.cek_at""",
                          (no, tid, tno, tgl, h, v.get("status") or "", v.get("kurir") or "", t1))
    except Exception as e:                                    # dicatat; dicoba lagi pada putaran jadwal berikutnya
        log["error"] = str(e)[:300]
    finally:
        log["selesai"] = now_ms()
        with tx() as c:
            semua = get_set(c, "validasi_log", {})
            semua[tgl] = log
            put_set(c, "validasi_log", dict(sorted(semua.items())[-30:]))
            sup = [r["email"] for r in c.execute("SELECT email, peran FROM hadir WHERE last_seen >= ?", (now_ms() - 30 * 86400000,))
                   if "supervisor" in json.loads(r["peran"])]
        _VALIDASI_JALAN.clear()
    if not log["error"] and log["masalah"] and sup:
        try:
            _voyage("/api/v1/hooks/fleet/kabari", {"to": sup, "app": "qhouse", "url": PUBLIC_URL + "/supervisor",
                                                   "title": "Q-House: %d pesanan tidak cocok dengan BigSeller (%s)" % (log["masalah"], tgl),
                                                   "body": "Dari %d nomor pesanan yang diserah-terimakan, %d tidak ditemukan / belum diserahkan "
                                                           "ke kurir / batal di BigSeller. Lihat tab Supervisor." % (log["total"], log["masalah"])},
                    timeout=20)
        except Exception:
            pass
    return True


def penjadwal():
    """Tiap menit: setelah JAM_VALIDASI WIB, validasi tanggal kemarin sekali (gagal -> dicoba lagi menit berikutnya, maks. 1x/15 mnt)."""
    while True:
        try:
            t = time.time() + WIB
            jam = int(time.strftime("%H", time.gmtime(t)))
            kemarin = time.strftime("%Y-%m-%d", time.gmtime(t - 86400))
            if jam >= JAM_VALIDASI:
                with tx() as c:
                    lg = get_set(c, "validasi_log", {}).get(kemarin)
                if not lg or (lg.get("error") and now_ms() - lg.get("selesai", 0) > 15 * 60000):
                    jalankan_validasi(kemarin)
        except Exception as e:
            print("penjadwal validasi:", e, flush=True)
        time.sleep(60)


# ---------- staf ----------
def state(c, me):
    hari = tgl_wib(now_ms())
    rows = c.execute("SELECT * FROM tiket WHERE tgl=? OR status IN ('menunggu','disiapkan','siap','bermasalah')"
                     " OR (status='dibatalkan' AND ready_at IS NOT NULL AND returned=0) ORDER BY created_at", (hari,)).fetchall()
    vlog = get_set(c, "validasi_log", {})
    akhir = max(vlog) if vlog else None
    masalah = [dict(r) for r in c.execute("SELECT * FROM validasi WHERE tgl=? AND hasil<>'cocok' ORDER BY tiket_no", (akhir,))] if akhir else []
    return {"me": me, "config": get_config(c), "tickets": [baris(r) for r in rows], "now": now_ms(),
            "sorterAktif": sorter_aktif(c), "publicUrl": PUBLIC_URL, "voyageUrl": VOYAGE_URL, "layanan": LAYANAN,
            "validasi": {"log": [vlog[k] for k in sorted(vlog, reverse=True)[:7]], "masalah": masalah[:300],
                         "jalan": _VALIDASI_JALAN.is_set(), "siap": bool(VOYAGE_INTERNAL and VOYAGE_KEY), "jam": JAM_VALIDASI}}


def aksi(c, b, me):
    """Satu pintu aksi staf. -> (http, body). Izin per aksi dari peran me['peran']."""
    a = b.get("aksi")
    peran = set(me["peran"])
    oleh = me["nama"]
    perlu = {"done": "sorter", "scan": "loket", "hapus_scan": "loket", "serah": "loket",
             "loket_buka": "supervisor", "loket_layanan": "supervisor", "batas": "supervisor", "batal": "supervisor",
             "kembali_rak": "supervisor", "lepas": "supervisor", "validasi": "supervisor"}.get(a)
    if not perlu:
        return 400, {"error": "Aksi tidak dikenal"}
    if perlu not in peran:
        return 403, {"error": "Akunmu tidak punya peran %s di Q-House." % perlu}
    cfg = get_config(c)
    if a == "validasi":
        tgl = str(b.get("tgl") or "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", tgl):
            return 400, {"error": "Tanggal tidak valid"}
        if not (VOYAGE_INTERNAL and VOYAGE_KEY):
            return 409, {"error": "Validasi belum disambungkan ke Voyage (QH_VOYAGE_INTERNAL / QH_VOYAGE_KEY)."}
        if _VALIDASI_JALAN.is_set():
            return 409, {"error": "Validasi sedang berjalan."}
        threading.Thread(target=jalankan_validasi, args=(tgl, oleh), daemon=True).start()
        return 200, {"ok": True, "pesan": "Validasi %s dimulai" % tgl}
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
    if a == "done":
        if t["status"] != "disiapkan":
            return 409, {"error": "Tiket ini tidak sedang disiapkan."}
        if t.get("sorter_email") != me["email"] and "supervisor" not in peran:
            return 403, {"error": "Tiket ini dibagikan ke sorter lain (%s)." % (t["sorter"] or "-")}
        t["loket"] = pilih_loket(t["layanan"], cfg)
        set_config(c, cfg)
        t["status"], t["ready_at"], t["siap_by"] = "siap", now_ms(), oleh
        catat(t, ("Done sorter %s — siap di Loket %d" % (oleh, t["loket"])) if t["loket"] else "Done sorter %s — belum ada loket dibuka" % oleh, oleh)
    elif a == "scan":
        if t["status"] != "siap":
            return 409, {"error": "%s belum siap diserahkan." % t["no"]}
        no = norm_pesanan(b.get("no"))
        if not NO_PESANAN.match(no):
            return 400, {"error": "Nomor pesanan tidak valid. Pindai ulang barcode nomor pesanan di label."}
        if no in t["pesanan"]:
            return 409, {"error": "Nomor pesanan %s sudah dipindai di tiket ini." % no}
        if len(t["pesanan"]) >= len(t["bookings"]):
            return 409, {"error": "Jumlah paket sudah lengkap (%d). Hapus pindaian yang salah dulu." % len(t["bookings"])}
        for r in c.execute("SELECT no, pesanan FROM tiket WHERE id<>? AND (status IN ('siap','selesai')) AND tgl>=?",
                           (t["id"], tgl_wib(now_ms() - 3 * 86400000))):
            if no in json.loads(r["pesanan"] or "[]"):
                return 409, {"error": "Nomor pesanan %s sudah tercatat di tiket %s." % (no, r["no"])}
        t["pesanan"].append(no)
        catat(t, "Nomor pesanan %s dipindai" % no, oleh)
    elif a == "hapus_scan":
        i = int(b.get("i", -1))
        if t["status"] != "siap" or not 0 <= i < len(t["pesanan"]):
            return 409, {"error": "Pindaian tidak ditemukan."}
        catat(t, "Pindaian %s dihapus" % t["pesanan"].pop(i), oleh)
    elif a == "serah":
        n = int(b.get("loket") or 0)
        if t["status"] != "siap":
            return 409, {"error": "%s berstatus %s, belum bisa diserahkan." % (t["no"], t["status"])}
        if t["loket"] != n:
            return 409, {"error": "%s untuk Loket %s. Arahkan driver ke sana." % (t["no"], t["loket"] or "–")}
        if len(t["pesanan"]) != len(t["bookings"]):
            return 409, {"error": "Pindai nomor pesanan semua paket dulu (%d dari %d)." % (len(t["pesanan"]), len(t["bookings"]))}
        t["status"], t["done_at"], t["done_by"] = "selesai", now_ms(), oleh
        catat(t, "Diserahkan di Loket %d" % n, oleh)
    elif a == "lepas":
        if t["status"] != "disiapkan":
            return 409, {"error": "Tiket ini tidak sedang di sorter."}
        catat(t, "Ditarik dari sorter %s oleh supervisor" % (t["sorter"] or ""), oleh)
        t["status"], t["sorter"], t["sorter_email"], t["taken_at"] = "menunggu", None, None, None
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
    if a == "lepas":
        bagikan(c)
    return 200, {"ok": True}


def me_dari(h):
    role = (h.get("X-Voyage-Role") or "viewer").strip().lower()
    peran = set(PERAN.get(role, set()))
    if h.get("X-Voyage-Administrator") == "1":
        peran = {"sorter", "loket", "supervisor"}
    return {"nama": urllib.parse.unquote(h.get("X-Voyage-Name") or "") or (h.get("X-Voyage-Email") or "Staf"),
            "email": (h.get("X-Voyage-Email") or "").strip().lower(), "role": role, "peran": sorted(peran)}


# ---------- HTTP ----------
# Publik (driver dari QR) HANYA halaman tiket per layanan + berkas yang dipakainya. Jalur lain (konsol staf, staff.js, dst.)
# wajib gate — Caddy membuka jalur yang sama persis (lihat Caddyfile); server menegakkannya lagi.
PUBLIK_HALAMAN = re.compile(r"^/antri/(instant|sameday)/?$")
PUBLIK_STATIC = re.compile(r"^(driver\.js|app\.css|vendor/lucide\.min\.js|assets/[a-z0-9\-]+\.png)$")


def jalur(p):
    """Jalur permintaan tanpa prefix QH_BASE: /qhouse/antri/instant -> /antri/instant, /qhouse -> /."""
    if BASE and (p == BASE or p.startswith(BASE + "/")):
        return p[len(BASE):] or "/"
    return p


def html_dengan_base(data):
    """Halaman HTML: berkas statis & JS memakai prefix QH_BASE (meta qh-base dibaca staff.js / driver.js)."""
    t = data.decode("utf-8")
    t = t.replace('"/static/', '"%s/static/' % BASE)
    t = t.replace("<head>", '<head>\n<meta name="qh-base" content="%s">' % BASE, 1)
    return t.encode("utf-8")


class H(BaseHTTPRequestHandler):
    server_version = "QHouse/2"

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
        if p.endswith(".html"):
            data = html_dengan_base(data)
        ct = mimetypes.guess_type(p)[0] or "application/octet-stream"
        if ct.startswith("text/") or ct.endswith("javascript"):
            ct += "; charset=utf-8"
        self.out(200, data, ct, {"Cache-Control": "public, max-age=300"} if cache else None)

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
        p = jalur(u.path)
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
                rawat(c)
                r = c.execute("SELECT * FROM tiket WHERE token=?", (tok,)).fetchone() if len(tok) >= 16 else None
                res = tiket_publik(c, baris(r)) if r else None
            return self.out(200, res) if res else self.out(404, {"error": "Tiket tidak ditemukan"})
        if not self.gated():
            return
        if p in ("/", "/sorter", "/loket", "/supervisor", "/barcode"):
            return self.berkas("staff.html")
        if p == "/api/state":
            me = me_dari(self.headers)
            tab = (urllib.parse.parse_qs(u.query).get("tab") or [""])[0][:20]
            with tx() as c:
                hadir_catat(c, me, tab)
                rawat(c)
                res = state(c, me)
            return self.out(200, res)
        self.out(404, {"error": "tidak ada"})

    def do_POST(self):
        p = jalur(urllib.parse.urlparse(self.path).path)
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
                rawat(c)
                code, res = aksi(c, b, me_dari(self.headers))
            return self.out(code, res)
        self.out(404, {"error": "tidak ada"})


def main():
    if NEED_GATE and len(GATE_SECRET) < 24:
        raise SystemExit("GATE_SECRET wajib diisi (>= 24 karakter) — sama dengan voyage-gate")
    init()
    threading.Thread(target=penjadwal, daemon=True).start()
    port = int(os.environ.get("PORT", "8000"))
    print("Q-House jalan di :%d (data %s; validasi BigSeller %s)" % (port, DB_PATH, "siap" if VOYAGE_INTERNAL and VOYAGE_KEY else "BELUM tersambung"),
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    main()
