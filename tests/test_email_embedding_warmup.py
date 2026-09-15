from types import SimpleNamespace

import numpy as np

from app.email_embedding_cache import EmbeddingCache, EmbeddingCacheKey
from app.email_embedding_classifier import CategoryDescription
from app.email_embedding_warmup import warm_frozen_training_embeddings


def test_warmup_caches_selected_inputs_and_descriptions_once(tmp_path):
    cache = EmbeddingCache(tmp_path, dimension=2)
    snapshot = {
        "input_schema_version": "input-v3",
        "observations": [
            {
                "stable_message_identity": "selected",
                "normalized_model_input": "selected mail",
            },
            {
                "stable_message_identity": "unselected",
                "normalized_model_input": "unselected mail",
            },
        ],
    }
    descriptions = {
        "work": CategoryDescription(
            core="Business work",
            include=("Projects",),
            exclude=("Personal",),
            version="work-v1",
        )
    }

    class Client:
        calls = []

        def embed(self, texts):
            self.calls.append(tuple(texts))
            return SimpleNamespace(
                vectors=np.array(
                    [[float(index), 1.0] for index, _ in enumerate(texts)],
                    dtype=np.float32,
                )
            )

    client = Client()
    result = warm_frozen_training_embeddings(
        cache=cache,
        embedding_client=client,
        snapshot=snapshot,
        descriptions=descriptions,
        embedding_model_id="jina-small",
        embedding_revision="r1",
        selected_message_identities=("selected",),
    )

    assert result.cache_writes == 4
    assert client.calls == [("selected mail", "Business work", "Projects", "Personal")]
    assert (
        cache.get(
            EmbeddingCacheKey.for_text(
                normalized_text="selected mail",
                input_schema_version="input-v3",
                embedding_model_id="jina-small",
                embedding_revision="r1",
            )
        )
        is not None
    )
    assert (
        cache.get(
            EmbeddingCacheKey.for_text(
                normalized_text="unselected mail",
                input_schema_version="input-v3",
                embedding_model_id="jina-small",
                embedding_revision="r1",
            )
        )
        is None
    )
    repeat = warm_frozen_training_embeddings(
        cache=cache,
        embedding_client=client,
        snapshot=snapshot,
        descriptions=descriptions,
        embedding_model_id="jina-small",
        embedding_revision="r1",
        selected_message_identities=("selected",),
    )
    assert repeat.cache_writes == 0
    assert len(client.calls) == 1
