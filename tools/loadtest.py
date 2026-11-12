"""200 ta bir vaqtli o'quvchi: login -> boshlash -> avtosaqlash (har 10 s) -> yuborish. Ustoz paneli ham so'raladi.
usage: loadtest.py <base_url> <state.json> <n_users> <exam_seconds> <teacher_pw>"""
import asyncio, json, random, re, sys, time
import httpx

BASE, STATE, N, EXAM_S, TPW = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
rows = json.load(open(STATE, encoding="utf-8"))["rows"]
extra = json.load(open(STATE.replace("state.json", "extra_users.json"), encoding="utf-8")) if N > len(rows) else []
users = ([(r[2], r[3]) for r in rows] + [(e[0], e[1]) for e in extra])[:N]
lat = {}
errors = []


def rec(name, dt):
    lat.setdefault(name, []).append(dt)


async def call(c, method, url, name, **kw):
    t = time.perf_counter()
    try:
        r = await c.request(method, url, **kw)
        rec(name, time.perf_counter() - t)
        if r.status_code >= 400:
            errors.append(f"{name} HTTP {r.status_code}")
        return r
    except Exception as e:
        rec(name, time.perf_counter() - t)
        errors.append(f"{name} {type(e).__name__}")
        return None


async def student(login, pw):
    async with httpx.AsyncClient(base_url=BASE, timeout=60, follow_redirects=True, verify=False) as c:
        await asyncio.sleep(random.uniform(0, 10))  # hamma 10 soniya ichida kiradi
        r = await call(c, "POST", "/login", "login", data={"login": login, "password": pw})
        if r is None or "Boshlash" not in r.text:
            errors.append("login failed " + login); return
        r = await call(c, "POST", "/s/start", "start")
        if r is None or 'class="q"' not in r.text:
            errors.append("start failed " + login); return
        ids = re.findall(r'data-id="(\d+)"', r.text)
        end = time.time() + EXAM_S
        answers = {k: "" for k in ids}
        while time.time() < end:
            await asyncio.sleep(random.uniform(8, 12))
            for k in ids:
                answers[k] += "Bu savolga javob matni, o'quvchi yozayotgan jumla. " * random.randint(1, 3)
            await call(c, "POST", "/api/save", "save", json={"answers": answers})
            if random.random() < 0.05:
                await call(c, "POST", "/api/violation", "violation", json={"kind": "paste"})
        await call(c, "POST", "/api/save", "final", json={"answers": answers, "final": True})
        await call(c, "GET", "/s", "results_page")


async def teacher():
    async with httpx.AsyncClient(base_url=BASE, timeout=60, follow_redirects=True, verify=False) as c:
        await call(c, "POST", "/login", "t_login", data={"login": "ustoz", "password": TPW})
        end = time.time() + EXAM_S + 15
        while time.time() < end:
            await call(c, "GET", "/t", "t_dashboard")
            await asyncio.sleep(5)


async def main():
    t0 = time.time()
    await asyncio.gather(teacher(), *[student(l, p) for l, p in users])
    print(f"\n=== {len(users)} o'quvchi, imtihon {EXAM_S}s, jami {time.time()-t0:.0f}s ===")
    for name, v in sorted(lat.items()):
        v.sort()
        q = lambda f: v[min(len(v) - 1, int(len(v) * f))]
        print(f"{name:14s} n={len(v):5d}  p50={q(.5)*1000:7.0f}ms  p95={q(.95)*1000:7.0f}ms  max={v[-1]*1000:7.0f}ms")
    print("xatolar:", len(errors), errors[:8])


asyncio.run(main())
