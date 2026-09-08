import math


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def srgb_to_linear(c):
    c = c / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c):
    c = _clamp(c, 0.0, 1.0)
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


_LIN_MIN = 0.02
_LIN_MAX = 0.98


def rgb_to_km(rgb):
    out = []
    for c in rgb:
        lin = srgb_to_linear(c)
        lin = _LIN_MIN if lin < _LIN_MIN else _LIN_MAX if lin > _LIN_MAX else lin
        out.append((1.0 - lin) ** 2 / (2.0 * lin))
    return out


def km_to_rgb(km):
    out = []
    for k in km:
        k = max(k, 0.0)
        r = 1.0 + k - math.sqrt(k * k + 2.0 * k)
        out.append(linear_to_srgb(r) * 255.0)
    return out


def mix_rgb(weights, palette_rgb, palette_km=None):
    if palette_km is None:
        palette_km = [rgb_to_km(c) for c in palette_rgb]
    km = [0.0, 0.0, 0.0]
    for w, pkm in zip(weights, palette_km):
        if w <= 0.0:
            continue
        km[0] += w * pkm[0]
        km[1] += w * pkm[1]
        km[2] += w * pkm[2]
    return km_to_rgb(km)


def _f_lab(t):
    delta = 6.0 / 29.0
    if t > delta ** 3:
        return t ** (1.0 / 3.0)
    return t / (3.0 * delta * delta) + 4.0 / 29.0


def srgb_to_lab(rgb):
    r = srgb_to_linear(rgb[0])
    g = srgb_to_linear(rgb[1])
    b = srgb_to_linear(rgb[2])
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    fx = _f_lab(x / 0.95047)
    fy = _f_lab(y / 1.0)
    fz = _f_lab(z / 1.08883)
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    bb = 200.0 * (fy - fz)
    return L, a, bb


def delta_e_2000(lab1, lab2):
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    Cbar7 = Cbar ** 7
    G = 0.5 * (1.0 - math.sqrt(Cbar7 / (Cbar7 + 25.0 ** 7)))
    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = math.hypot(a1p, b1)
    C2p = math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360.0 if C1p > 0.0 else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360.0 if C2p > 0.0 else 0.0
    dLp = L2 - L1
    dCp = C2p - C1p
    if C1p * C2p == 0.0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180.0:
        dhp = h2p - h1p
    elif h2p - h1p > 180.0:
        dhp = h2p - h1p - 360.0
    else:
        dhp = h2p - h1p + 360.0
    dHp = 2.0 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2.0))
    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    if C1p * C2p == 0.0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180.0:
        hbp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360.0:
        hbp = (h1p + h2p + 360.0) / 2.0
    else:
        hbp = (h1p + h2p - 360.0) / 2.0
    T = (1.0
         - 0.17 * math.cos(math.radians(hbp - 30.0))
         + 0.24 * math.cos(math.radians(2.0 * hbp))
         + 0.32 * math.cos(math.radians(3.0 * hbp + 6.0))
         - 0.20 * math.cos(math.radians(4.0 * hbp - 63.0)))
    dTheta = 30.0 * math.exp(-(((hbp - 275.0) / 25.0) ** 2))
    Rc = 2.0 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25.0 ** 7))
    Sl = 1.0 + 0.015 * (Lbp - 50.0) ** 2 / math.sqrt(20.0 + (Lbp - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cbp
    Sh = 1.0 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2.0 * dTheta)) * Rc
    return math.sqrt(
        (dLp / Sl) ** 2
        + (dCp / Sc) ** 2
        + (dHp / Sh) ** 2
        + Rt * (dCp / Sc) * (dHp / Sh))


def _least_squares_subset(A, b, columns):
    k = len(columns)
    M = [[A[i][columns[j]] for j in range(k)] for i in range(len(A))]
    rhs = b[:]
    for j in range(k):
        normx = 0.0
        for i in range(j, len(M)):
            normx += M[i][j] ** 2
        normx = math.sqrt(normx)
        if normx < 1e-15:
            continue
        if M[j][j] >= 0.0:
            normx = -normx
        M[j][j] -= normx
        vnorm2 = 0.0
        for i in range(j, len(M)):
            vnorm2 += M[i][j] ** 2
        if vnorm2 < 1e-30:
            M[j][j] = normx
            continue
        for c in range(j + 1, k):
            dot = 0.0
            for i in range(j, len(M)):
                dot += M[i][j] * M[i][c]
            dot *= 2.0 / vnorm2
            for i in range(j, len(M)):
                M[i][c] -= dot * M[i][j]
        dot = 0.0
        for i in range(j, len(M)):
            dot += M[i][j] * rhs[i]
        dot *= 2.0 / vnorm2
        for i in range(j, len(M)):
            rhs[i] -= dot * M[i][j]
        M[j][j] = normx
    solution = [0.0] * k
    rank = min(k, len(M))
    for r in range(rank - 1, -1, -1):
        if abs(M[r][r]) <= 1e-14:
            solution[r] = 0.0
            continue
        total = rhs[r]
        for c in range(r + 1, k):
            total -= M[r][c] * solution[c]
        solution[r] = total / M[r][r]
    return {columns[p]: solution[p] for p in range(k)}


def nnls(A, b, max_iter=500, tol=1e-12):
    m = len(A)
    n = len(A[0])
    x = [0.0] * n
    passive = []
    active = list(range(n))
    w = [0.0] * n
    for j in range(n):
        total = 0.0
        for i in range(m):
            total += A[i][j] * b[i]
        w[j] = total
    iterations = 0
    while active and iterations < max_iter:
        iterations += 1
        j = max(active, key=lambda idx: w[idx])
        if w[j] <= tol:
            break
        active.remove(j)
        passive.append(j)
        while True:
            if not passive:
                break
            xp = _least_squares_subset(A, b, passive)
            if min(xp.values()) >= -tol:
                for k, v in xp.items():
                    x[k] = v
                break
            alpha = 1.0
            for k in passive:
                if xp[k] <= -tol:
                    ratio = x[k] / (x[k] - xp[k])
                    if ratio < alpha:
                        alpha = ratio
            for k in passive:
                x[k] += alpha * (xp[k] - x[k])
            for k in passive:
                if abs(x[k]) <= tol:
                    active.append(k)
            for k in [k for k in passive if abs(x[k]) <= tol]:
                passive.remove(k)
        ax = [0.0] * m
        for j in range(n):
            if x[j] != 0.0:
                for i in range(m):
                    ax[i] += A[i][j] * x[j]
        for j in range(n):
            total = 0.0
            for i in range(m):
                total += A[i][j] * (b[i] - ax[i])
            w[j] = total
    return x


def _refine(weights, target_lab, palette_rgb, palette_km, extra):
    active = [i for i, x in enumerate(weights) if x > 1e-8]
    if not active:
        active = [0]
    if extra not in active:
        active.append(extra)
    n_sub = len(active)
    sub_rgb = [palette_rgb[i] for i in active]
    sub_km = [palette_km[i] for i in active]
    best = [weights[i] for i in active]
    total = sum(best)
    if total <= 1e-12:
        best = [1.0] + [0.0] * (n_sub - 1)
    else:
        best = [x / total for x in best]

    def score(w):
        mixed = mix_rgb(w, sub_rgb, sub_km)
        de = delta_e_2000(target_lab, srgb_to_lab(mixed))
        nonzero = sum(1 for x in w if x > 1e-6)
        return de + 0.1 * max(0, nonzero - 1)

    best_score = score(best)
    step = 0.25
    while step > 1e-4:
        improved = False
        for i in range(n_sub):
            for delta in (step, -step):
                candidate = best[:]
                candidate[i] = max(0.0, candidate[i] + delta)
                total = sum(candidate)
                if total <= 1e-12:
                    continue
                candidate = [x / total for x in candidate]
                value = score(candidate)
                if value < best_score - 1e-9:
                    best = candidate
                    best_score = value
                    improved = True
        if not improved:
            step *= 0.5
    out = [0.0] * len(weights)
    for i, x in zip(active, best):
        out[i] = x
    return out


def _result(weights, palette_rgb, target_rgb, target_lab):
    mixed = mix_rgb(weights, palette_rgb)
    dominant = max(range(len(weights)), key=lambda i: weights[i])
    if weights[dominant] >= 0.995:
        paint = palette_rgb[dominant]
        if delta_e_2000(target_lab, srgb_to_lab(paint)) < delta_e_2000(target_lab, srgb_to_lab(mixed)):
            mixed = paint
    de = delta_e_2000(target_lab, srgb_to_lab(mixed))
    return {"weights": weights, "mixed": mixed, "de": de, "exact": de <= 0.5}


_palette_cache = {}


def _palette_data(palette_rgb):
    key = tuple(palette_rgb)
    data = _palette_cache.get(key)
    if data is None:
        data = ([rgb_to_km(c) for c in palette_rgb],
                [srgb_to_lab(c) for c in palette_rgb])
        if len(_palette_cache) > 16:
            _palette_cache.clear()
        _palette_cache[key] = data
    return data


def analyze(target_rgb, palette_rgb):
    n = len(palette_rgb)
    if n == 0:
        raise ValueError("empty palette")
    target_lab = srgb_to_lab(target_rgb)
    palette_km, palette_labs = _palette_data(palette_rgb)
    if n == 1:
        return _result([1.0], palette_rgb, target_rgb, target_lab)
    distances = [delta_e_2000(target_lab, lab) for lab in palette_labs]
    closest = min(range(n), key=lambda i: distances[i])
    if distances[closest] <= 0.5:
        weights = [0.0] * n
        weights[closest] = 1.0
        return _result(weights, palette_rgb, target_rgb, target_lab)
    A = [[palette_km[j][i] for j in range(n)] for i in range(3)]
    b = rgb_to_km(target_rgb)
    scale = 10.0 * (1.0 + max(max(row) for row in A))
    Aaug = [row[:] for row in A]
    Aaug.append([scale] * n)
    baug = b[:] + [scale]
    initial = nnls(Aaug, baug)
    total = sum(initial)
    if total <= 1e-12:
        initial = [0.0] * n
        initial[closest] = 1.0
    else:
        initial = [x / total for x in initial]
    weights = _refine(initial, target_lab, palette_rgb, palette_km, closest)
    return _result(weights, palette_rgb, target_rgb, target_lab)
