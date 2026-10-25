"""Neural network (TensorFlow) forecasting the OURCs Isis flag 1-5 days ahead.

At 07:00 on day D, predicts the 07:00 flag on each of days D+1 ... D+5 (Green,
Light Blue, Dark Blue, Amber, Red or Black) from river flows/levels, rain
(observed and forecast) and recent flags. One network has a softmax head per
horizon; an ensemble of networks is averaged. See features.py for the inputs and
the no-lookahead guarantees, experiments.py for the evaluation.

Run: py fetch_data.py, then py neuralnet.py
"""

from pathlib import Path

import numpy as np
import tensorflow as tf

from features import CLASSES, HORIZONS, RIVER_RAIN_FLAG, build_dataset, check_no_lookahead, load_data

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "model"

SEED = 42
VALIDATION_FRACTION = 0.2
MAX_STEPS = 1000
PATIENCE = 60

# Best configuration from experiments.py.
FEATURES = RIVER_RAIN_FLAG
ENSEMBLE = 5  # Average this many networks trained with different seeds.


def build_model(X_train):
    norm = tf.keras.layers.Normalization()
    norm.adapt(X_train)
    reg = tf.keras.regularizers.l2(1e-3)
    return tf.keras.Sequential([
        tf.keras.Input(shape=(X_train.shape[1],)),
        norm,
        tf.keras.layers.Dense(32, activation="relu", kernel_regularizer=reg),
        tf.keras.layers.Dropout(0.2),
        tf.keras.layers.Dense(16, activation="relu", kernel_regularizer=reg),
        tf.keras.layers.Dense(HORIZONS * len(CLASSES)),
        tf.keras.layers.Reshape((HORIZONS, len(CLASSES))),
        tf.keras.layers.Softmax(),
    ])


def masked_loss(Y, probs):
    """Cross-entropy averaged over the known targets (Y >= 0) only."""
    mask = tf.cast(Y >= 0, tf.float32)
    ce = tf.keras.losses.sparse_categorical_crossentropy(tf.maximum(Y, 0), probs)
    return tf.reduce_sum(ce * mask) / tf.reduce_sum(mask)


def fit(X, Y, seed=SEED):
    """Train one model; returns a function mapping X -> probabilities (n, HORIZONS, len(CLASSES)).

    Uses a compiled full-batch training loop (much faster than model.fit on tiny
    data) with early stopping on the most recent 20% of the training days.
    """
    tf.keras.utils.set_random_seed(seed)
    split = int(len(X) * (1 - VALIDATION_FRACTION))
    X_train, Y_train = tf.constant(X[:split]), tf.constant(Y[:split])
    X_val, Y_val = tf.constant(X[split:]), tf.constant(Y[split:])

    model = build_model(X[:split])
    optimizer = tf.keras.optimizers.Adam(1e-2)

    @tf.function
    def step():
        with tf.GradientTape() as tape:
            loss = masked_loss(Y_train, model(X_train, training=True)) + tf.add_n(model.losses)
        optimizer.apply_gradients(zip(tape.gradient(loss, model.trainable_variables), model.trainable_variables))
        return masked_loss(Y_val, model(X_val, training=False))

    best, best_weights, since_best = np.inf, model.get_weights(), 0
    for _ in range(MAX_STEPS):
        val_loss = float(step())
        if val_loss < best - 1e-4:
            best, best_weights, since_best = val_loss, model.get_weights(), 0
        else:
            since_best += 1
            if since_best >= PATIENCE:
                break
    model.set_weights(best_weights)

    def predict_proba(X_new):
        return model(X_new, training=False).numpy()

    predict_proba.model = model
    return predict_proba


def fit_ensemble(X, Y, size=ENSEMBLE, seed=SEED):
    """Train `size` networks with different seeds; returns a function averaging their probabilities."""
    members = [fit(X, Y, seed=seed + i) for i in range(size)]

    def predict_proba(X_new):
        return np.mean([m(X_new) for m in members], axis=0)

    predict_proba.models = [m.model for m in members]
    return predict_proba


def load_ensemble():
    """Load the saved ensemble; returns a function mapping X -> probabilities."""
    models = [tf.keras.models.load_model(p) for p in sorted(MODEL_DIR.glob("neuralnet_*.keras"))]
    if not models:
        raise FileNotFoundError("No saved models in model/: run py neuralnet.py first")
    return lambda X_new: np.mean([m(X_new, training=False).numpy() for m in models], axis=0)


def main():
    flags, ea, fc = load_data()
    check_no_lookahead(flags, ea, fc)
    X, Y, _, _, columns = build_dataset(flags, ea, fc, FEATURES)
    print(f"{len(X)} days, {len(columns)} features, {HORIZONS} horizons, ensemble of {ENSEMBLE}")

    final = fit_ensemble(X, Y)
    MODEL_DIR.mkdir(exist_ok=True)
    for old in MODEL_DIR.glob("neuralnet_*.keras"):
        old.unlink()
    for i, model in enumerate(final.models):
        model.save(MODEL_DIR / f"neuralnet_{i}.keras")
    print(f"Saved {len(final.models)} models trained on all days to {MODEL_DIR.relative_to(ROOT)}/")
    print("Run py experiments.py for the evaluation, py forecast.py for the live forecast.")


if __name__ == "__main__":
    main()
