import shutil, os
for d in [r"E:\work\partner\build", r"E:\work\partner\dist"]:
    shutil.rmtree(d, ignore_errors=True)
    print(f"Removed {d}")
for root, dirs, files in os.walk(r"E:\work\partner"):
    if "__pycache__" in dirs:
        p = os.path.join(root, "__pycache__")
        shutil.rmtree(p, ignore_errors=True)
        print(f"Removed {p}")
print("ALL CLEARED")
