import os
import shutil
import sys

# Add backend directory to sys.path so app module can be found
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.source.git_handler import GitSourceHandler

def test_git():
    handler = GitSourceHandler()
    url = "https://github.com/octocat/Hello-World.git"
    dest = "./temp_git_repo"

    if os.path.exists(dest):
        import stat
        def remove_readonly(func, path, excinfo):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(dest, onerror=remove_readonly)

    print(f"Fetching from git... URL: {url}")
    handler.fetch(url, dest)

    print("Calculating hash...")
    hash_val = handler.get_version_hash(dest)
    print(f"Git Version Hash: {hash_val}")

    if os.path.exists(dest):
        import stat
        def remove_readonly(func, path, excinfo):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(dest, onerror=remove_readonly)

if __name__ == "__main__":
    try:
        test_git()
    except Exception as e:
        import traceback
        traceback.print_exc()
