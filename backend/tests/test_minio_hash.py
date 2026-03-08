import os
import shutil
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.source.minio_handler import MinioSourceHandler

def test_minio_hash():
    handler = MinioSourceHandler()
    
    dest1 = "./temp_minio_1"
    dest2 = "./temp_minio_2"
    
    os.makedirs(os.path.join(dest1, "folder"), exist_ok=True)
    os.makedirs(os.path.join(dest2, "folder"), exist_ok=True)
    
    with open(os.path.join(dest1, "folder", "test.py"), "w") as f:
        f.write("print('hello')")
        
    with open(os.path.join(dest2, "folder", "test.py"), "w") as f:
        f.write("print('hello')")
        
    hash1 = handler.get_version_hash(dest1)
    hash2 = handler.get_version_hash(dest2)
    
    print(f"Hash 1: {hash1}")
    print(f"Hash 2: {hash2}")
    if hash1 == hash2:
        print("SUCCESS! Hashes match for same content.")
    else:
        print("FAIL! Hashes do not match.")

if __name__ == "__main__":
    try:
        test_minio_hash()
    except Exception as e:
        import traceback
        traceback.print_exc()
