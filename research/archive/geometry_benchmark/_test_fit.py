"""Self-test for fit_similarity and apply_T. Research tool."""
import math
import matcher as M


def check(T_true, eps, label):
    s, th, tx, ty = T_true
    pts = [(10.0, 20.0), (30.0, 50.0), (5.0, 7.0), (200.0, 5.0),
           (-40.0, 90.0), (77.0, -13.0)]
    pairs = []
    for u, v in pts:
        X, Y = M.apply_T((s, th, tx, ty, eps), u, v)
        pairs.append((u, v, X, Y))
    f = M.fit_similarity(pairs, eps)
    if f is None:
        print("%-28s FAIL: None" % label)
        return
    fs, fth, ftx, fty = f
    err = max(abs(fs - s) / s, abs(fth - th), abs(ftx - tx), abs(fty - ty))
    print("%-28s s=%.9f (exact %.9f)  th=%+.9f (exact %+.9f)  "
          "t=(%.6f, %.6f) (exact %.6f, %.6f)  maxrelerr=%.3e"
          % (label, fs, s, fth, th, ftx, fty, tx, ty, err))


check((0.706062, 0.0, 25484818.9, 6677513.3), +1, "no reflection, th=0")
check((0.706062, 0.226893, 25484818.9, 6677513.3), +1, "no reflection, th=13deg")
check((0.706062, -0.5, 100.0, 200.0), -1, "reflection, th=-28.6deg")
check((1.416318, 1.8, -25484818.9, 6677513.3), +1, "no reflection, th=103deg")
check((0.3333, 0.0, 0.0, 0.0), -1, "reflection, th=0")