import numpy as np

from app.email_fasttext_model import FastTextEmailClassifier


def test_single_prediction_uses_public_batch_api():
    class Model:
        def get_labels(self):
            return ["__label__work", "__label__legal"]

        def predict(self, texts, *, k):
            assert texts == ["subject work"]
            assert k == 2
            return [["__label__work", "__label__legal"]], [[0.8, 0.2]]

    classifier = FastTextEmailClassifier()
    classifier._model = Model()

    prediction = classifier.predict("subject work")

    assert prediction.label == "work"
    assert prediction.probabilities == {"work": 0.8, "legal": 0.2}


def test_training_initializes_entire_hashed_feature_matrix():
    classifier = FastTextEmailClassifier().fit(
        ["subject work train", "subject legal train"], ["work", "legal"]
    )

    matrix = classifier._model.get_input_matrix()

    assert np.isfinite(matrix).all()
    # The native initializer partitions the matrix into ten blocks. A single
    # thread left nine blocks untouched, including hashed word-ngram features.
    assert np.count_nonzero(np.any(matrix != 0, axis=1)) >= len(matrix) - 1
    for text in ("subject work train", "subject legal train"):
        assert all(np.isfinite(value) for value in classifier.predict(text).probabilities.values())
