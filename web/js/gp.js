// Gaussian-process prediction in the browser.
// Mirrors pvdhub/model.py::gp_predict line by line, using the parameters the
// Python pipeline exported (standardisation, ARD length scales, alpha, Cholesky L).

export function prepare(m) {
  return {
    ...m,
    Xt: m.X_train.map((r) => Float64Array.from(r)),
    Lr: m.L.map((r) => Float64Array.from(r)),
    a: Float64Array.from(m.alpha),
    ls: Float64Array.from(m.length_scales),
  };
}

export function predict(pm, x) {
  const d = x.length, n = pm.Xt.length;
  const xs = new Float64Array(d);
  for (let j = 0; j < d; j++) xs[j] = (x[j] - pm.x_mean[j]) / pm.x_scale[j];
  const k = new Float64Array(n);
  let mean = 0;
  for (let i = 0; i < n; i++) {
    let d2 = 0;
    const row = pm.Xt[i];
    for (let j = 0; j < d; j++) { const z = (xs[j] - row[j]) / pm.ls[j]; d2 += z * z; }
    k[i] = pm.const * Math.exp(-0.5 * d2);
    mean += k[i] * pm.a[i];
  }
  // forward substitution: L v = k
  const v = new Float64Array(n);
  let vv = 0;
  for (let i = 0; i < n; i++) {
    let sum = k[i];
    const Li = pm.Lr[i];
    for (let j = 0; j < i; j++) sum -= Li[j] * v[j];
    v[i] = sum / Li[i];
    vv += v[i] * v[i];
  }
  const variance = Math.max(pm.const - vv, 1e-12) * pm.y_std * pm.y_std;
  return { mean: mean * pm.y_std + pm.y_mean, std: Math.sqrt(variance) };
}

// standard normal pdf / cdf (Abramowitz & Stegun 7.1.26, |error| < 1.5e-7)
const pdf = (z) => Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI);
function cdf(z) {
  const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t
    * Math.exp(-(z * z) / 2);
  return z >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y);
}

export function expectedImprovement(mean, std, best, xi = 0.05) {
  const z = (mean - best - xi) / std;
  return (mean - best - xi) * cdf(z) + std * pdf(z);
}
