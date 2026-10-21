# RAG evaluation 20261021T032626Z (git unknown)

Dataset: `evals/golden/rag_golden.jsonl` — 35 questions (28 answerable; dev 16 / test 19). Embeddings: `BAAI/bge-small-en-v1.5`; reranker: `Xenova/ms-marco-MiniLM-L-6-v2`; K=5.

| Configuration | Recall@5 | Precision@5 | MRR | nDCG@5 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|
| dense | 0.964 | 0.236 | 0.898 | 0.891 | 16.6 | 32.3 |
| lexical | 0.881 | 0.214 | 0.746 | 0.759 | 7.5 | 9.6 |
| hybrid_rrf | 0.946 | 0.229 | 0.863 | 0.872 | 16.0 | 27.2 |
| dense+rerank | 0.982 | 0.243 | 0.946 | 0.939 | 337.7 | 397.6 |
| hybrid_rrf+rerank | 0.982 | 0.243 | 0.946 | 0.939 | 355.7 | 407.1 |
| hybrid_rrf+rerank@20 | 0.982 | 0.243 | 0.946 | 0.938 | 193.8 | 214.1 |
| hybrid_rrf+rerank@10 | 0.982 | 0.243 | 0.946 | 0.938 | 79.8 | 123.2 |

## Evidence-sufficiency (abstention) calibration

| Signal | Threshold (fit on dev) | Dev bal. acc. | Test answerable kept | Test unanswerable abstained |
|---|---|---|---|---|
| hybrid_rrf+rerank: top_rerank_score | -4.4705 | 1.0 | 1.0 (15) | 1.0 (4) |
| dense: top_dense_distance | 0.3667 | 1.0 | 1.0 (15) | 1.0 (4) |

Permission checks passed: **True**

Generation: not requested 

_Latency measured in-process on a single developer machine; not a production benchmark._
