"""Machine learning over a dataset, in plain numpy (Analytics -> ML lab, and "ml" tiles on a dashboard).

A visual with an "ml" part is answered by engine.run like any other visual (same joins, filters and permissions); the
rows then go through run() here:

    {"model": "linear", "target": "Amount", "features": ["Qty", "Branch"], "params": {...}, "predict": {"Qty": 3, "Branch": "LHR"}}

Models: linear, ridge, poly, tree, forest, knn (a number to predict), logistic, tree, forest, knn (a category to predict),
kmeans, pca, anomaly, correlation, forecast. Nothing is stored: a model is trained on the current data on every ask
(at most MAX_ROWS sampled records), with a fixed seed, so the same data gives the same answer.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np

MAX_ROWS = 10_000
TREE_ROWS = 4_000
MAX_FEATURES = 20
MAX_CLASSES = 12
TOP_LEVELS = 10
POINTS = 600
SEED = 42
SUPERVISED = ("linear", "ridge", "poly", "logistic", "tree", "forest", "knn")
MODELS = (*SUPERVISED, "kmeans", "pca", "anomaly", "correlation", "forecast")
PARAMS = {"test": (0.1, 0.5, 0.25), "alpha": (0.0, 10_000.0, 1.0), "degree": (2, 4, 2), "depth": (1, 12, 6), "trees": (5, 60, 25),
          "k": (1, 50, 7), "clusters": (0, 12, 0), "sensitivity": (1.5, 6.0, 3.0), "horizon": (1, 60, 6), "season": (0, 52, 0)}
WHOLE = {"degree", "depth", "trees", "k", "clusters", "horizon", "season"}


class MlError(ValueError):
    """Something the person can fix (shown in the visual)."""


# ------------------------------------------------------------------ the request
def clean(value: Any, where: str) -> dict[str, Any] | None:
    if value in (None, {}, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    model = str(value.get("model") or "")
    if model not in MODELS:
        raise ValueError(f"{where}.model must be one of {', '.join(MODELS)}")
    features = value.get("features") or []
    if not isinstance(features, list) or len(features) > MAX_FEATURES or any(not isinstance(item, str) or len(item) > 120 for item in features):
        raise ValueError(f"{where}.features must be a list of at most {MAX_FEATURES} column names")
    target = str(value.get("target") or "")[:120]
    if model in SUPERVISED and not target:
        raise ValueError(f"{where}.target: choose the column to predict")
    params = value.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError(f"{where}.params must be an object")
    out_params: dict[str, float | int] = {}
    for name, (low, high, _default) in PARAMS.items():
        if params.get(name) in (None, ""):
            continue
        try:
            number = float(params[name])
        except (TypeError, ValueError):
            raise ValueError(f"{where}.params.{name} must be a number") from None
        if not math.isfinite(number):
            raise ValueError(f"{where}.params.{name} must be a number")
        number = max(low, min(high, number))
        out_params[name] = int(number) if name in WHOLE else number
    out = {"model": model, "target": target, "features": list(dict.fromkeys(features)), "params": out_params}
    if params.get("bucket") in ("year", "quarter", "month", "week", "day"):
        out["params"]["bucket"] = params["bucket"]
    predict = value.get("predict")
    if predict:
        if not isinstance(predict, dict) or len(predict) > MAX_FEATURES or any(isinstance(v, (dict, list)) for v in predict.values()):
            raise ValueError(f"{where}.predict must be an object of column: value")
        out["predict"] = {str(key)[:120]: (item[:200] if isinstance(item, str) else item) for key, item in predict.items()}
    return out


def _param(config: dict[str, Any], name: str) -> Any:
    return config["params"].get(name, PARAMS[name][2])


# ------------------------------------------------------------------ numbers from records
def _number(value: Any) -> float:
    if value is None:
        return math.nan
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else math.nan
    if isinstance(value, datetime):
        return value.timestamp() / 86400 if value.tzinfo else (value - datetime(1970, 1, 1)).total_seconds() / 86400
    if isinstance(value, date):
        return float((value - date(1970, 1, 1)).days)
    text = str(value).strip()
    try:
        number = float(text)
        return number if math.isfinite(number) else math.nan
    except ValueError:
        pass
    try:
        return _number(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return math.nan


def _text(value: Any) -> str:
    return "(blank)" if value is None or value == "" else str(value)[:60]


class Encoder:
    """Columns -> a standardised matrix: numbers and dates as they are (blank = median), text as its commonest values."""

    def __init__(self, names: list[str], kinds: dict[str, str], data: dict[str, list[Any]]):
        self.parts: list[dict[str, Any]] = []
        for name in names:
            values = data[name]
            if kinds.get(name) in ("number", "date", "bool"):
                numbers = np.array([_number(v) for v in values], float)
                seen = numbers[~np.isnan(numbers)]
                fill = float(np.median(seen)) if len(seen) else 0.0
                filled = np.where(np.isnan(numbers), fill, numbers)
                std = float(filled.std())
                self.parts.append({"name": name, "kind": kinds[name], "fill": fill, "mean": float(filled.mean()), "std": std or 1.0,
                                   "low": float(filled.min()) if len(filled) else 0.0, "high": float(filled.max()) if len(filled) else 0.0})
            else:
                counts: dict[str, int] = {}
                for value in values:
                    counts[_text(value)] = counts.get(_text(value), 0) + 1
                levels = sorted(counts, key=lambda item: (-counts[item], item))
                self.parts.append({"name": name, "kind": "text", "levels": levels[:TOP_LEVELS], "total": len(levels)})
        self.owner = [part["name"] for part in self.parts for _ in (part.get("levels") or [0])]
        self.labels = [label for part in self.parts
                       for label in ([f"{part['name']} = {level}" for level in part["levels"]] if part["kind"] == "text" else [part["name"]])]

    def matrix(self, data: dict[str, list[Any]]) -> np.ndarray:
        columns = []
        for part in self.parts:
            values = data[part["name"]]
            if part["kind"] == "text":
                texts = [_text(v) for v in values]
                columns += [np.array([text == level for text in texts], float) for level in part["levels"]]
            else:
                numbers = np.array([_number(v) for v in values], float)
                columns.append((np.where(np.isnan(numbers), part["fill"], numbers) - part["mean"]) / part["std"])
        return np.column_stack(columns) if columns else np.zeros((0, 0))


# ------------------------------------------------------------------ models (fit -> predict function)
def _ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    penalty = np.eye(design.shape[1]) * max(alpha, 1e-8)
    penalty[0, 0] = 0
    return np.linalg.lstsq(design.T @ design + penalty, design.T @ y, rcond=None)[0]


def _poly(x: np.ndarray, owner_numeric: list[int], degree: int) -> np.ndarray:
    extra = [np.clip(x[:, i], -6, 6) ** power for i in owner_numeric for power in range(2, degree + 1)]
    extra += [x[:, a] * x[:, b] for i, a in enumerate(owner_numeric[:6]) for b in owner_numeric[i + 1:6]]
    return np.column_stack([x, *extra]) if extra else x


def _softmax(scores: np.ndarray) -> np.ndarray:
    scores = scores - scores.max(1, keepdims=True)
    exp = np.exp(scores)
    return exp / exp.sum(1, keepdims=True)


def _logistic(x: np.ndarray, y: np.ndarray, classes: int, alpha: float) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    weights = np.zeros((design.shape[1], classes))
    target = np.eye(classes)[y]
    step = 1.0 / (0.5 * max(np.linalg.norm(design, 2) ** 2 / len(design), 1e-9) + alpha / len(design))
    velocity = np.zeros_like(weights)
    for turn in range(400):                      # accelerated gradient descent
        ahead = weights + velocity * (turn / (turn + 3))
        gradient = design.T @ (_softmax(design @ ahead) - target) / len(design)
        gradient[1:] += alpha / len(design) * ahead[1:]
        new = ahead - step * gradient
        velocity = new - weights
        weights = new
        if np.abs(gradient).max() < 1e-5:
            break
    return weights


class Tree:
    """CART. `y` is a matrix: one column (a number) or one per class (0/1); variance / Gini is the same sum of squares."""

    def __init__(self, depth: int, leaf: int, tries: int | None = None, rng: np.random.Generator | None = None):
        self.depth, self.leaf, self.tries, self.rng = depth, leaf, tries, rng
        self.nodes: list[tuple] = []

    def fit(self, x: np.ndarray, y: np.ndarray) -> Tree:
        self.nodes = []
        self._grow(x, y, np.arange(len(x)), 0)
        return self

    def _grow(self, x: np.ndarray, y: np.ndarray, rows: np.ndarray, level: int) -> int:
        index = len(self.nodes)
        mean = y[rows].mean(0)
        self.nodes.append((-1, 0.0, -1, -1, mean, len(rows)))
        if level >= self.depth or len(rows) < 2 * self.leaf or np.allclose(y[rows], mean):
            return index
        options = np.arange(x.shape[1]) if self.tries is None else self.rng.choice(x.shape[1], self.tries, replace=False)
        best, choice, total = 1e-9, None, y[rows].sum(0)
        count = len(rows)
        base = float((total ** 2).sum()) / count
        for feature in options:
            order = np.argsort(x[rows, feature], kind="stable")
            values = x[rows[order], feature]
            left = np.cumsum(y[rows[order]], axis=0)[:-1]
            size = np.arange(1, count)
            allowed = (values[1:] > values[:-1]) & (size >= self.leaf) & (count - size >= self.leaf)
            if not allowed.any():
                continue
            gain = (left ** 2).sum(1) / size + ((total - left) ** 2).sum(1) / (count - size) - base
            gain[~allowed] = -1
            at = int(gain.argmax())
            if gain[at] > best:
                best, choice = float(gain[at]), (int(feature), float((values[at] + values[at + 1]) / 2))
        if choice is None:
            return index
        goes_left = x[rows, choice[0]] <= choice[1]
        left_node = self._grow(x, y, rows[goes_left], level + 1)
        right_node = self._grow(x, y, rows[~goes_left], level + 1)
        self.nodes[index] = (*choice, left_node, right_node, mean, len(rows))
        return index

    def predict(self, x: np.ndarray) -> np.ndarray:
        out = np.zeros((len(x), len(self.nodes[0][4])))
        todo = [(0, np.arange(len(x)))]
        while todo:
            node, rows = todo.pop()
            feature, threshold, left, right, mean, _count = self.nodes[node]
            if feature < 0 or not len(rows):
                out[rows] = mean
                continue
            mask = x[rows, feature] <= threshold
            todo += [(left, rows[mask]), (right, rows[~mask])]
        return out


def _knn(train: np.ndarray, y: np.ndarray, k: int) -> Any:
    def predict(x: np.ndarray) -> np.ndarray:
        out = np.zeros((len(x), y.shape[1]))
        near = min(k, len(train))
        for start in range(0, len(x), 500):
            part = x[start:start + 500]
            distance = (part ** 2).sum(1)[:, None] + (train ** 2).sum(1)[None] - 2 * part @ train.T
            picked = np.argpartition(distance, near - 1, axis=1)[:, :near]
            out[start:start + 500] = y[picked].mean(1)
        return out
    return predict


def _fit(config: dict[str, Any], x: np.ndarray, y: np.ndarray, numeric: list[int]) -> tuple[Any, np.ndarray | None]:
    """(predict function over the matrix, linear coefficients or None). y: one column, or one 0/1 column per class."""
    model, rng = config["model"], np.random.default_rng(SEED)
    if model in ("linear", "ridge", "poly"):
        degree = _param(config, "degree")
        shape = (lambda m: _poly(m, numeric, degree)) if model == "poly" else (lambda m: m)
        weights = _ridge(shape(x), y[:, 0], 0.0 if model == "linear" else _param(config, "alpha"))
        return (lambda m: (np.column_stack([np.ones(len(m)), shape(m)]) @ weights)[:, None]), (weights[1:] if model != "poly" else None)
    if model == "logistic":
        weights = _logistic(x, y.argmax(1), y.shape[1], _param(config, "alpha"))
        coefficients = weights[1:, 1] - weights[1:, 0] if y.shape[1] == 2 else None
        return (lambda m: _softmax(np.column_stack([np.ones(len(m)), m]) @ weights)), coefficients
    if model == "knn":
        return _knn(x, y, _param(config, "k")), None
    if len(x) > TREE_ROWS:
        keep = rng.choice(len(x), TREE_ROWS, replace=False)
        x, y = x[keep], y[keep]
    leaf = max(2, len(x) // 400)
    if model == "tree":
        return Tree(_param(config, "depth"), leaf).fit(x, y).predict, None
    tries = max(1, min(x.shape[1], round(math.sqrt(x.shape[1])) if y.shape[1] > 1 else math.ceil(x.shape[1] / 3)))
    trees = []
    for _ in range(_param(config, "trees")):
        rows = rng.integers(0, len(x), len(x))
        trees.append(Tree(_param(config, "depth"), leaf, tries, rng).fit(x[rows], y[rows]))
    return (lambda m: np.mean([tree.predict(m) for tree in trees], axis=0)), None


# ------------------------------------------------------------------ measures
def _regression_scores(actual: np.ndarray, guess: np.ndarray) -> dict[str, float]:
    error = actual - guess
    spread = float(((actual - actual.mean()) ** 2).sum())
    return {"r2": 1 - float((error ** 2).sum()) / spread if spread > 0 else 0.0,
            "rmse": float(np.sqrt((error ** 2).mean())), "mae": float(np.abs(error).mean())}


def _class_scores(actual: np.ndarray, guess: np.ndarray, classes: int) -> dict[str, float]:
    f1 = []
    for index in range(classes):
        hit = int(((actual == index) & (guess == index)).sum())
        wrong = int(((actual != index) & (guess == index)).sum())
        missed = int(((actual == index) & (guess != index)).sum())
        if hit + missed:
            f1.append(2 * hit / (2 * hit + wrong + missed))
    common = float(np.bincount(actual, minlength=classes).max() / len(actual))
    return {"accuracy": float((actual == guess).mean()), "f1": float(np.mean(f1)) if f1 else 0.0, "baseline": common}


def _importance(predict: Any, x: np.ndarray, score: Any, encoder: Encoder) -> list[dict[str, Any]]:
    """How much worse the model gets when one column is shuffled."""
    rng = np.random.default_rng(SEED)
    if len(x) > 1500:
        keep = rng.choice(len(x), 1500, replace=False)
    else:
        keep = np.arange(len(x))
    base = score(predict(x[keep]), keep)
    out = []
    for part in encoder.parts:
        columns = [i for i, name in enumerate(encoder.owner) if name == part["name"]]
        mixed = x[keep].copy()
        mixed[:, columns] = mixed[rng.permutation(len(keep))][:, columns]
        out.append({"name": part["name"], "value": max(0.0, base - score(predict(mixed), keep))})
    total = sum(item["value"] for item in out) or 1.0
    return sorted(({"name": item["name"], "value": item["value"] / total} for item in out), key=lambda item: -item["value"])


def _sample(count: int) -> np.ndarray:
    return np.sort(np.random.default_rng(SEED).choice(count, POINTS, replace=False)) if count > POINTS else np.arange(count)


def _round(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _round(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(item) for item in value]
    if isinstance(value, np.ndarray):
        return _round(value.tolist())
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return float(f"{value:.6g}") if math.isfinite(value) else None
    return value


# ------------------------------------------------------------------ each kind of analysis
def _supervised(config: dict[str, Any], data: dict[str, list[Any]], kinds: dict[str, str]) -> dict[str, Any]:
    target, model = config["target"], config["model"]
    features = [name for name in config["features"] if name != target]
    if not features:
        raise MlError("Choose at least one column to predict from")
    number_target = kinds.get(target) in ("number", "date")
    if model in ("linear", "ridge", "poly") and kinds.get(target) != "number":
        raise MlError(f"'{target}' is not a number: for a category use logistic regression, a decision tree, a random forest or KNN")
    distinct = {_text(v) for v in data[target] if v not in (None, "")}
    classify = model == "logistic" or not number_target
    if classify and len(distinct) > MAX_CLASSES:
        raise MlError(f"'{target}' has {len(distinct)} different values: at most {MAX_CLASSES} categories can be predicted"
                      + (" (it is a number: use a regression model)" if kinds.get(target) == "number" else ""))
    if classify and len(distinct) < 2:
        raise MlError(f"'{target}' has only one value, so there is nothing to predict")
    usable = [i for i, value in enumerate(data[target]) if (value not in (None, "") if classify else not math.isnan(_number(value)))]
    if len(usable) < 12:
        raise MlError(f"Only {len(usable)} records have a value in '{target}': at least 12 are needed")
    data = {name: [values[i] for i in usable] for name, values in data.items()}
    order = np.random.default_rng(SEED).permutation(len(usable))
    cut = max(3, int(len(usable) * _param(config, "test")))
    test, train = np.sort(order[:cut]), np.sort(order[cut:])
    encoder = Encoder(features, kinds, {name: [data[name][i] for i in train] for name in features})
    x = encoder.matrix(data)
    numeric = [i for i, label in enumerate(encoder.labels) if label == encoder.owner[i]]
    levels: list[str] = []
    if classify:
        texts = [_text(v) for v in data[target]]
        levels = sorted(set(texts), key=lambda item: (-texts.count(item), item))
        codes = np.array([levels.index(item) for item in texts])
        y = np.eye(len(levels))[codes]
    else:
        y = np.array([_number(v) for v in data[target]], float)[:, None]
    predict, coefficients = _fit(config, x[train], y[train], numeric)
    guess = predict(x[test])
    out: dict[str, Any] = {"task": "classification" if classify else "regression", "target": target, "features": features,
                           "trainRows": len(train), "testRows": len(test)}
    if classify:
        picked = guess.argmax(1)
        out["metrics"] = _class_scores(codes[test], picked, len(levels))
        matrix = np.zeros((len(levels), len(levels)), int)
        np.add.at(matrix, (codes[test], picked), 1)
        out["chart"] = {"kind": "confusion", "labels": levels, "matrix": matrix.tolist()}
        out["importance"] = _importance(predict, x[test], lambda p, keep: float((p.argmax(1) == codes[test][keep]).mean()), encoder)
    else:
        actual = y[test][:, 0]
        out["metrics"] = _regression_scores(actual, guess[:, 0])
        out["trainMetrics"] = _regression_scores(y[train][:, 0], predict(x[train])[:, 0])
        shown = _sample(len(test))
        single = encoder.parts[0] if len(encoder.parts) == 1 and encoder.parts[0]["kind"] in ("number", "date") else None
        if single:
            spots = np.linspace(single["low"], single["high"], 60)
            line = predict(((spots - single["mean"]) / single["std"])[:, None])[:, 0]
            values = x[test][:, 0] * single["std"] + single["mean"]
            out["chart"] = {"kind": "fit", "x": single["name"], "y": target, "points": np.column_stack([values[shown], actual[shown]]),
                            "line": np.column_stack([spots, line])}
        else:
            out["chart"] = {"kind": "actual", "x": f"Actual {target}", "y": f"Predicted {target}",
                            "points": np.column_stack([actual[shown], guess[shown, 0]])}
        out["importance"] = _importance(predict, x[test], lambda p, keep: -float(((p[:, 0] - actual[keep]) ** 2).mean()), encoder)
    if coefficients is not None:
        scale = [next(p for p in encoder.parts if p["name"] == owner) for owner in encoder.owner]
        out["coefficients"] = sorted(({"name": label, "perUnit": float(c) / (part["std"] if part["kind"] != "text" else 1.0), "weight": float(c)}
                                      for label, c, part in zip(encoder.labels, coefficients, scale, strict=True)), key=lambda item: -abs(item["weight"]))[:30]
    out["inputs"] = [{"name": p["name"], "kind": p["kind"], "values": p.get("levels"), "typical": p.get("levels", [None])[0] if p["kind"] == "text" else p["fill"],
                      "low": p.get("low"), "high": p.get("high")} for p in encoder.parts]
    if config.get("predict"):
        row = {p["name"]: [config["predict"].get(p["name"], p["levels"][0] if p["kind"] == "text" and p["levels"] else None)] for p in encoder.parts}
        answer = predict(encoder.matrix(row))[0]
        out["prediction"] = ({"value": levels[int(answer.argmax())], "chances": [{"name": level, "value": float(answer[i])} for i, level in enumerate(levels)]}
                             if classify else {"value": float(answer[0]), "range": out["metrics"]["rmse"]})
    out["summary"] = _supervised_summary(out)
    return out


def _supervised_summary(out: dict[str, Any]) -> str:
    metrics, best = out["metrics"], out["importance"][0]["name"] if out["importance"] else ""
    if out["task"] == "classification":
        better = metrics["accuracy"] - metrics["baseline"]
        text = f"It guesses '{out['target']}' right for {metrics['accuracy']:.0%} of the records it had not seen (always guessing the commonest value: {metrics['baseline']:.0%})."
        text += " That is not better than guessing: try other columns." if better <= 0.01 else ""
    else:
        text = f"It explains {max(0.0, metrics['r2']):.0%} of the differences in '{out['target']}'; a prediction is off by {metrics['mae']:,.4g} on average."
        if metrics["r2"] < 0.1:
            text += " These columns hardly explain it: try other columns or another model."
        elif out["trainMetrics"]["r2"] - metrics["r2"] > 0.25:
            text += " It fits the training records much better than new ones (overfitting): use fewer columns or a simpler model."
    return text + (f" Most important: {best}." if best else "")


def _matrix(config: dict[str, Any], data: dict[str, list[Any]], kinds: dict[str, str], least: int = 1) -> tuple[Encoder, np.ndarray]:
    features = config["features"]
    if len(features) < least:
        raise MlError(f"Choose at least {least} column{'s' if least > 1 else ''}")
    if len(next(iter(data.values()), [])) < 12:
        raise MlError("At least 12 records are needed")
    encoder = Encoder(features, kinds, data)
    return encoder, encoder.matrix(data)


def _components(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    centred = x - x.mean(0)
    _u, s, vt = np.linalg.svd(centred, full_matrices=False)
    share = s ** 2 / max(float((s ** 2).sum()), 1e-12)
    two = vt[:2]
    points = centred @ two.T
    if points.shape[1] < 2:
        points = np.column_stack([points, np.zeros(len(points))])
    return points, share, vt


def _distance(x: np.ndarray, centres: np.ndarray) -> np.ndarray:
    return np.maximum((x ** 2).sum(1)[:, None] + (centres ** 2).sum(1)[None] - 2 * x @ centres.T, 0)


def _kmeans_once(x: np.ndarray, k: int, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    centres = x[[int(rng.integers(len(x)))]]
    for _ in range(k - 1):                       # k-means++
        distance = _distance(x, centres).min(1)
        chance = distance / distance.sum() if distance.sum() > 0 else None
        centres = np.vstack([centres, x[int(rng.choice(len(x), p=chance))]])
    labels = np.zeros(len(x), int)
    for _ in range(60):
        distance = _distance(x, centres)
        new = distance.argmin(1)
        centres = np.vstack([x[new == i].mean(0) if (new == i).any() else centres[i] for i in range(k)])
        if (new == labels).all():
            break
        labels = new
    return labels, float(((x - centres[labels]) ** 2).sum())


def _silhouette(x: np.ndarray, labels: np.ndarray) -> float:
    keep = np.random.default_rng(SEED).choice(len(x), min(len(x), 500), replace=False)
    x, labels = x[keep], labels[keep]
    distance = np.sqrt(_distance(x, x))
    scores = []
    for i, own in enumerate(labels):
        same = labels == own
        if same.sum() < 2:
            continue
        inside = distance[i, same].sum() / (same.sum() - 1)
        others = [distance[i, labels == other].mean() for other in set(labels.tolist()) - {own}]
        if others:
            scores.append((min(others) - inside) / max(inside, min(others), 1e-12))
    return float(np.mean(scores)) if scores else 0.0


def _kmeans(config: dict[str, Any], data: dict[str, list[Any]], kinds: dict[str, str]) -> dict[str, Any]:
    encoder, x = _matrix(config, data, kinds)
    wanted = _param(config, "clusters")
    options = [wanted] if wanted >= 2 else list(range(2, min(8, len(x) // 3) + 1))
    rng = np.random.default_rng(SEED)
    best: tuple[float, int, np.ndarray] | None = None
    tried = []
    for k in options:
        labels, _cost = min((_kmeans_once(x, k, rng) for _ in range(4)), key=lambda item: item[1])
        score = _silhouette(x, labels)
        tried.append({"clusters": k, "score": score})
        if best is None or score > best[0]:
            best = (score, k, labels)
    score, k, labels = best
    sizes = np.bincount(labels, minlength=k)
    ranked = np.argsort(-sizes)
    place = {int(old): new for new, old in enumerate(ranked)}
    labels = np.array([place[int(item)] for item in labels])
    points, share, _vt = _components(x)
    shown = _sample(len(x))
    rows = []
    for index in range(k):
        mask = np.flatnonzero(labels == index)
        row: list[Any] = [f"Group {index + 1}", int(len(mask)), len(mask) / len(labels)]
        for part in encoder.parts:
            values = [data[part["name"]][i] for i in mask]
            if part["kind"] == "text":
                texts = [_text(v) for v in values]
                top = max(set(texts), key=texts.count) if texts else ""
                row.append(f"{top} ({texts.count(top) / max(len(texts), 1):.0%})")
            else:
                numbers = np.array([_number(v) for v in values], float)
                mean = float(np.nanmean(numbers)) if len(numbers) and not np.isnan(numbers).all() else math.nan
                row.append(_day(mean) if part["kind"] == "date" else mean)
        rows.append(row)
    strength = "clearly different" if score > 0.5 else "fairly different" if score > 0.25 else "hardly different (overlapping)"
    return {"task": "clustering", "features": config["features"], "metrics": {"groups": k, "silhouette": score}, "tried": tried,
            "chart": {"kind": "groups", "x": f"Direction 1 ({share[0]:.0%})", "y": f"Direction 2 ({share[1] if len(share) > 1 else 0:.0%})",
                      "points": [[*points[i], int(labels[i])] for i in shown], "names": [f"Group {i + 1}" for i in range(k)]},
            "table": {"columns": ["Group", "Records", "Share", *[part["name"] for part in encoder.parts]], "rows": rows},
            "summary": f"The records fall into {k} groups that are {strength}. The biggest has {sizes.max() / len(labels):.0%} of the records."}


def _pca(config: dict[str, Any], data: dict[str, list[Any]], kinds: dict[str, str]) -> dict[str, Any]:
    encoder, x = _matrix(config, data, kinds, 2)
    points, share, vt = _components(x)
    shown = _sample(len(x))
    rows = [[label, *(float(vt[d][i]) if d < len(vt) else 0.0 for d in range(2))] for i, label in enumerate(encoder.labels)]
    rows.sort(key=lambda row: -max(abs(row[1]), abs(row[2])))
    two = float(share[:2].sum())
    return {"task": "pca", "features": config["features"], "metrics": {"explained": two}, "components": [float(item) for item in share[:10]],
            "chart": {"kind": "groups", "x": f"Direction 1 ({share[0]:.0%})", "y": f"Direction 2 ({share[1] if len(share) > 1 else 0:.0%})",
                      "points": [[*points[i], 0] for i in shown], "names": ["Records"]},
            "table": {"columns": ["Column", "Direction 1", "Direction 2"], "rows": rows[:30]},
            "summary": f"Two directions keep {two:.0%} of the differences between the records. Direction 1 is mostly {rows[0][0]}."}


def _day(days: float) -> str | None:
    return None if math.isnan(days) else (date(1970, 1, 1) + timedelta(days=int(days))).isoformat()


def _anomaly(config: dict[str, Any], data: dict[str, list[Any]], kinds: dict[str, str]) -> dict[str, Any]:
    numeric = [name for name in config["features"] if kinds.get(name) in ("number", "date")]
    if not numeric:
        raise MlError("Choose at least one number column")
    encoder, _x = _matrix({**config, "features": numeric}, data, kinds)
    raw = np.column_stack([[_number(v) for v in data[name]] for name in numeric])
    centre = np.nanmedian(raw, axis=0)
    spread = np.nanmedian(np.abs(raw - centre), axis=0) * 1.4826
    fallback = np.nanstd(raw, axis=0)
    spread = np.where(spread > 0, spread, np.where(fallback > 0, fallback, 1.0))
    z = np.nan_to_num((raw - centre) / spread)
    if len(numeric) > 1 and len(z) > 5 * len(numeric):       # together: unusual combinations as well
        clipped = np.clip(z, -5, 5)
        inverse = np.linalg.pinv(np.cov(clipped.T) + np.eye(len(numeric)) * 1e-6)
        together = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", z, inverse, z), 0) / len(numeric))
        score = np.maximum(np.abs(z).max(1), together)
    else:
        score = np.abs(z).max(1)
    limit = float(_param(config, "sensitivity"))
    flagged = np.flatnonzero(score > limit)
    worst = flagged[np.argsort(-score[flagged])]
    rows = [[float(score[i]), numeric[int(np.abs(z[i]).argmax())], *[_text(data[name][i]) if kinds.get(name) == "text" else
             (float(_number(data[name][i])) if kinds.get(name) == "number" else _text(data[name][i])) for name in config["features"]]] for i in worst[:100]]
    shown = np.unique(np.concatenate([_sample(len(raw)), worst[:200]])).astype(int)
    second = numeric[1] if len(numeric) > 1 else None
    points = [[float(raw[i, 0]) if second else int(i), float(raw[i, 1]) if second else float(raw[i, 0]), int(score[i] > limit)] for i in shown]
    return {"task": "anomaly", "features": config["features"], "metrics": {"found": int(len(flagged)), "share": len(flagged) / len(raw)},
            "chart": {"kind": "groups", "x": numeric[0] if second else "Record", "y": second or numeric[0],
                      "points": [p for p in points if not (math.isnan(p[0]) or math.isnan(p[1]))], "names": ["Normal", "Unusual"]},
            "table": {"columns": ["Score", "Because of", *config["features"]], "rows": rows},
            "summary": f"{len(flagged):,} of {len(raw):,} records ({len(flagged) / len(raw):.1%}) are unusual. A score is how many normal spreads away from the middle; above {limit:g} is unusual."}


def _correlation(config: dict[str, Any], data: dict[str, list[Any]], kinds: dict[str, str]) -> dict[str, Any]:
    numeric = [name for name in config["features"] if kinds.get(name) in ("number", "date", "bool")]
    if len(numeric) < 2:
        raise MlError("Choose at least two number columns")
    encoder, x = _matrix({**config, "features": numeric}, data, kinds, 2)
    steady = x.std(0) == 0
    with np.errstate(invalid="ignore", divide="ignore"):
        matrix = np.nan_to_num(np.corrcoef(x.T))
    matrix[steady, :] = 0
    matrix[:, steady] = 0
    pairs = sorted(((abs(matrix[i, j]), i, j) for i in range(len(numeric)) for j in range(i + 1, len(numeric))), reverse=True)
    rows = [[numeric[i], numeric[j], float(matrix[i, j]), _strength(matrix[i, j])] for _size, i, j in pairs[:30]]
    first = rows[0]
    return {"task": "correlation", "features": numeric, "metrics": {"strongest": first[2]},
            "chart": {"kind": "correlation", "labels": numeric, "matrix": matrix},
            "table": {"columns": ["Column", "With", "Correlation", "Meaning"], "rows": rows},
            "summary": f"Strongest: {first[0]} and {first[1]} ({first[2]:+.2f}, {first[3]}). A correlation does not say which one causes the other."}


def _strength(value: float) -> str:
    size = abs(value)
    word = "very strong" if size >= 0.8 else "strong" if size >= 0.6 else "moderate" if size >= 0.4 else "weak" if size >= 0.2 else "none"
    return word if word == "none" else f"{word}, {'rise together' if value > 0 else 'one rises, the other falls'}"


# ------------------------------------------------------------------ forecast (Holt-Winters)
def _next_labels(labels: list[str], count: int) -> list[str]:
    last = labels[-1]
    out = []
    if re.fullmatch(r"\d{4}", last):
        return [str(int(last) + i) for i in range(1, count + 1)]
    match = re.fullmatch(r"(\d{4})-?\s?Q(\d)", last)
    if match:
        at = int(match[1]) * 4 + int(match[2]) - 1
        separator = last[4:last.index("Q")]
        return [f"{(at + i) // 4}{separator}Q{(at + i) % 4 + 1}" for i in range(1, count + 1)]
    match = re.fullmatch(r"(\d{4})-(\d{2})", last)
    if match:
        at = int(match[1]) * 12 + int(match[2]) - 1
        return [f"{(at + i) // 12}-{(at + i) % 12 + 1:02d}" for i in range(1, count + 1)]
    match = re.fullmatch(r"(\d{4})-?W(\d{2})", last)
    if match:
        day = date.fromisocalendar(int(match[1]), int(match[2]), 1)
        separator = last[4:last.index("W")]
        for i in range(1, count + 1):
            year, week, _ = (day + timedelta(weeks=i)).isocalendar()
            out.append(f"{year}{separator}W{week:02d}")
        return out
    try:
        days = [date.fromisoformat(item[:10]) for item in labels[-2:]]
        step = max(1, (days[1] - days[0]).days) if len(days) > 1 else 1
        return [(days[-1] + timedelta(days=step * i)).isoformat() for i in range(1, count + 1)]
    except ValueError:
        return [f"+{i}" for i in range(1, count + 1)]


def _holt_winters(y: np.ndarray, season: int, a: float, b: float, g: float, ahead: int, damp: float = 0.97) -> tuple[np.ndarray, np.ndarray]:
    """(one-step fitted values, forecast) with an additive season (0 = none) and a damped trend."""
    if season:
        level = float(y[:season].mean())
        trend = float((y[season:2 * season].mean() - level) / season)
        seasonal = list(y[:season] - level)
    else:
        level, trend, seasonal = float(y[0]), float(y[1] - y[0]) if len(y) > 1 else 0.0, []
    fitted = np.zeros(len(y))
    for t, value in enumerate(y):
        s = seasonal[t % season] if season else 0.0
        fitted[t] = level + damp * trend + s
        new = a * (value - s) + (1 - a) * (level + damp * trend)
        trend = b * (new - level) + (1 - b) * damp * trend
        if season:
            seasonal[t % season] = g * (value - new) + (1 - g) * s
        level = new
    out, push = [], 0.0
    for h in range(1, ahead + 1):
        push += damp ** h
        out.append(level + push * trend + (seasonal[(len(y) + h - 1) % season] if season else 0.0))
    return fitted, np.array(out)


def _forecast(config: dict[str, Any], rows: list[tuple]) -> dict[str, Any]:
    history = [(_text(row[0]), _number(row[1])) for row in rows if row[0] is not None]
    history = [(name, value) for name, value in history if not math.isnan(value)]
    if len(history) < 5:
        raise MlError(f"Only {len(history)} periods have a value: a forecast needs at least 5 (choose a smaller date step)")
    labels, y = [h[0] for h in history], np.array([h[1] for h in history], float)
    ahead, asked = _param(config, "horizon"), _param(config, "season")
    guess = {"month": [12], "quarter": [4], "week": [4, 13, 52], "day": [7]}.get(str(config["params"].get("bucket")), [])
    seasons = [asked] if asked >= 2 else [0, *guess]
    start_at = 2 if len(y) > 6 else 1
    best = None
    for season in seasons:
        if season and len(y) < 2 * season + 1:
            continue
        for a in (0.1, 0.3, 0.5, 0.7, 0.9):
            for b in (0.01, 0.1, 0.3):
                for g in ((0.05, 0.2, 0.5) if season else (0.0,)):
                    fitted, future = _holt_winters(y, season, a, b, g, ahead)
                    skip = max(start_at, season)
                    error = float(np.sqrt(((y[skip:] - fitted[skip:]) ** 2).mean()))
                    cost = error * (1.05 if season else 1.0)       # a season must earn its extra numbers
                    if best is None or cost < best[0]:
                        best = (cost, error, season, fitted, future)
    if best is None:
        raise MlError(f"A season of {asked} needs at least {2 * asked + 1} periods; there are {len(y)}")
    _cost, error, season, fitted, future = best
    band = 1.96 * error * np.sqrt(np.arange(1, ahead + 1) * 0.35 + 0.65)
    floor = 0.0 if (y >= 0).all() else -math.inf
    low, high = np.maximum(future - band, floor), np.maximum(future + band, floor)
    future = np.maximum(future, floor)
    nonzero = y != 0
    skip = max(start_at, season)
    used = nonzero[skip:]
    mape = float(np.abs((y[skip:] - fitted[skip:])[used] / y[skip:][used]).mean()) if used.any() else None
    change = (future[-1] - y[-1]) / abs(y[-1]) if y[-1] else 0.0
    names = _next_labels(labels, ahead)
    return {"task": "forecast", "metrics": {"rmse": error, "mape": mape, "season": season},
            "chart": {"kind": "forecast", "labels": labels[-400:], "values": y[-400:], "fitted": fitted[-400:], "nextLabels": names,
                      "next": future, "low": low, "high": high},
            "table": {"columns": ["Period", "Forecast", "Low", "High"], "rows": [[n, float(f), float(lo), float(hi)] for n, f, lo, hi in zip(names, future, low, high, strict=True)]},
            "summary": f"{names[-1]}: about {future[-1]:,.4g} ({change:+.0%} against {labels[-1]})."
                       + (f" A repeating season of {season} periods was found." if season else "")
                       + (f" One step ahead it was off by {mape:.0%} on average." if mape is not None else "")}


# ------------------------------------------------------------------ the answer of a visual
def run(config: dict[str, Any], columns: list[dict[str, Any]], rows: list[tuple]) -> dict[str, Any]:
    """Result of one analysis; MlError when it cannot be done with this data."""
    if not rows:
        raise MlError("There are no records for these filters")
    try:
        if config["model"] == "forecast":
            if len(columns) < 2:
                raise MlError("Choose a date column and a value")
            out = _forecast(config, rows)
        else:
            names = [column["name"] for column in columns]
            if len(set(names)) != len(names):
                raise MlError("Two of the columns have the same name: use columns of one dataset")
            missing = [name for name in [*config["features"], *([config["target"]] if config["model"] in SUPERVISED else [])] if name not in names]
            if missing:
                raise MlError(f"These columns are not in the data any more: {', '.join(missing)}")
            kinds = {column["name"]: column["kind"] for column in columns}
            data = {name: [row[i] for row in rows] for i, name in enumerate(names)}
            kind = config["model"]
            work = {"kmeans": _kmeans, "pca": _pca, "anomaly": _anomaly, "correlation": _correlation}.get(kind, _supervised)
            out = work(config, data, kinds)
    except np.linalg.LinAlgError:
        raise MlError("The numbers of these columns cannot be solved (all the same?): choose other columns") from None
    return _round({"model": config["model"], "rows": len(rows), **out})
