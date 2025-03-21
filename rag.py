import pprint
import os
import torch
import time
import numpy as np
from pymilvus import MilvusClient
from torch.nn import functional as F
from sentence_transformers import SentenceTransformer
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders.pdf import PyPDFLoader

COLLECTION_NAME = "pdfs"

def load_embedding_model():
    DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model_name = "BAAI/bge-large-en-v1.5"
    encoder = SentenceTransformer(model_name, device=DEVICE)
    embedding_dim = encoder.get_sentence_embedding_dimension()
    max_seq_length = encoder.get_max_seq_length()

    print(f"model_name: {model_name}")
    print(f"EMBEDDING_DIM: {embedding_dim}")
    print(f"MAX_SEQ_LENGTH: {max_seq_length}")
    return encoder, embedding_dim, max_seq_length

def load_file(pdf_path):
    # Check if file exists
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"File not found: {pdf_path}. Please provide a valid file path.")
    
    loader = PyPDFLoader(pdf_path)
    docs = loader.load()
    
    print(f"Loaded {len(docs)} documents from {pdf_path}")
    return docs

def encode_docs(docs, encoder):
   
    CHUNK_SIZE = 384  
    chunk_overlap = int(CHUNK_SIZE * 0.3)  
    
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", ", ", " ", ""]
    )

    chunks = child_splitter.split_documents(docs)
    print(f"{len(docs)} docs split into {len(chunks)} child documents.")

    list_of_strings = [doc.page_content for doc in chunks if hasattr(doc, 'page_content')]
    embeddings = torch.tensor(encoder.encode(list_of_strings))
    # Normalize each embedding vector individually
    embeddings = F.normalize(embeddings, p=2, dim=1).numpy().astype(np.float32)

    dict_list = []
    for chunk, vector in zip(chunks, embeddings):
        chunk_dict = {
            'chunk': chunk.page_content,
            'source': chunk.metadata.get('source', ''),
            'page': chunk.metadata.get('page', 0),  
            'vector': vector.tolist(),  # Convert to list for Milvus
        }
        dict_list.append(chunk_dict)
    return dict_list

def save_vectors_to_db(dict_list, embedding_dim):
    mc = MilvusClient("db/rag.db")
    mc.create_collection(COLLECTION_NAME, embedding_dim, consistency_level="Eventually", auto_id=True, overwrite=True)
    print("Start inserting entities")
    start_time = time.time()
    mc.insert(COLLECTION_NAME, data=dict_list, progress_bar=True)
    end_time = time.time()
    print(f"Milvus insert time for {len(dict_list)} vectors: {round(end_time - start_time, 2)} seconds")
    return mc

def search(mc, encoder, dict_list, question, top_k=5):  
    query_embeddings = encoder.encode(question)
    query_embeddings = torch.tensor(query_embeddings).unsqueeze(0)
    # Normalize and convert to Milvus-compatible format
    query_embeddings = F.normalize(query_embeddings, p=2, dim=1).numpy().astype(np.float32).tolist()

    OUTPUT_FIELDS = list(dict_list[0].keys())
    OUTPUT_FIELDS.remove('vector')

    results = mc.search(COLLECTION_NAME, data=query_embeddings, output_fields=OUTPUT_FIELDS, limit=top_k, consistency_level="Eventually")
    
    print(f"Search query: '{question}'")
    print(f"Retrieved {len(results[0])} results")
    
    # Print truncated versions of the first few results for debugging
    for i, res in enumerate(results[0][:2]): 
        if "chunk" in res["entity"]:
            chunk_preview = res["entity"]["chunk"][:100] + "..." if len(res["entity"]["chunk"]) > 100 else res["entity"]["chunk"]
            print(f"Result {i+1}: Distance={res['distance']:.4f}, Page={res['entity'].get('page', 'N/A')}")
            print(f"  Preview: {chunk_preview}")
    
    return results

def init_rag(pdf_path, force_reload=False):
    docs = load_file(pdf_path)
    encoder, embedding_dim, max_seq_length = load_embedding_model()
    dict_list = encode_docs(docs, encoder)
    mc = save_vectors_to_db(dict_list, embedding_dim)
    return mc, encoder, dict_list

def rag_search(mc, encoder, dict_list, question, top_k=5):
    return search(mc, encoder, dict_list, question, top_k)

if __name__ == "__main__":
    # Test code only runs when rag.py is executed directly
    pdf_path = "/home/ubuntu/ChatBot/SlamonetalSCIENCE1987.pdf"
    mc, encoder, dict_list = init_rag(pdf_path)
