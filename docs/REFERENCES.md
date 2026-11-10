# References

Every library, model, data source and learning resource used, with its official link and use.

## Libraries and tools
| Name | Link | Used for |
|---|---|---|
| Python 3.11 | https://www.python.org/downloads/release/python-3119/ | language |
| Ollama | https://ollama.com · https://github.com/ollama/ollama/blob/main/docs/api.md | local LLM runtime and REST API (`/api/tags`) |
| langchain-ollama (`ChatOllama`) | https://python.langchain.com/docs/integrations/chat/ollama/ | LLM client, `.bind_tools()` |
| LangChain text splitters | https://python.langchain.com/docs/how_to/split_by_token/ | `RecursiveCharacterTextSplitter.from_huggingface_tokenizer` |
| LangChain community loaders | https://python.langchain.com/docs/integrations/document_loaders/pymupdf/ | `PyMuPDFLoader` / `PyPDFLoader` |
| PyMuPDF | https://pymupdf.readthedocs.io | PDF text extraction, test PDFs, scanned-page check |
| sentence-transformers | https://sbert.net | running bge-m3 locally |
| PyTorch | https://pytorch.org/get-started/locally/ | GPU (CUDA) backend |
| ChromaDB | https://docs.trychroma.com | persistent vector DB, cosine space |
| Pydantic / pydantic-settings | https://docs.pydantic.dev · https://docs.pydantic.dev/latest/concepts/pydantic_settings/ | schemas and `.env` settings |
| Streamlit | https://docs.streamlit.io | chat UI |
| Ragas | https://docs.ragas.io | Faithfulness, Answer Relevancy, Context Recall |
| pytest | https://docs.pytest.org | tests |
| SQLite WAL | https://www.sqlite.org/wal.html | database journal mode |
| DSpace 7 REST API | https://wiki.lyrasis.org/display/DSDOC7x/REST+API | finding direct PDF links in WHO IRIS |

## Models
| Model | Link | Use |
|---|---|---|
| Qwen2.5-7B-Instruct (Ollama tag `qwen2.5:7b-instruct`) | https://ollama.com/library/qwen2.5 · https://huggingface.co/Qwen/Qwen2.5-7B-Instruct | answer generation, Ragas judge |
| BAAI/bge-m3 | https://huggingface.co/BAAI/bge-m3 | multilingual embeddings (8192-token window) |
| intfloat/multilingual-e5-base (fallback) | https://huggingface.co/intfloat/multilingual-e5-base | low-end laptops (512 tokens, needs prefixes) |

## Data sources (corpus)
Full per-file list with URLs and licences: `data/sources.csv`.
| Source | Link | Notes |
|---|---|---|
| WHO IRIS | https://iris.who.int | 20 WHO guidelines (17 EN, 3 AR) |
| NICE guidance | https://www.nice.org.uk/guidance | NG201, NG133, NG3, NG229 |
| U.S. FDA | https://www.fda.gov | NSAIDs 20-week safety communication, food-safety booklet, fish advice |
| MotherToBaby (OTIS) | https://mothertobaby.org/fact-sheets/ | medication fact sheets |

## Learning resources
| Resource | Link |
|---|---|
| LLM Zoomcamp RAG workshop (DataTalks.Club) | https://www.youtube.com/watch?v=q-p36Ak6YI8 |
| Learn RAG From Scratch (freeCodeCamp / Lance Martin) | https://www.youtube.com/watch?v=sVcwVQRHIc8 |
| RAG + LangChain (pixegami) | https://www.youtube.com/watch?v=tcqEUSNCn8I |
| Streamlit chat-app tutorial | https://docs.streamlit.io/develop/tutorials/chat-and-llm-apps/build-conversational-apps |
| Building and Evaluating Advanced RAG (DeepLearning.AI) | https://www.deeplearning.ai/short-courses/building-evaluating-advanced-rag/ |
| Building Agentic RAG with LlamaIndex (DeepLearning.AI) | https://www.deeplearning.ai/short-courses/building-agentic-rag-with-llamaindex/ |

## Regulations and background (to confirm current versions before citing in the report)
- Egyptian Personal Data Protection Law No. 151 of 2020.
- FDA Pregnancy and Lactation Labeling Rule (PLLR).
