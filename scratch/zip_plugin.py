import os
import zipfile

def build_zip():
    zip_name = 'Ebook-Translator.zip'
    exclude_dirs = {'.git', '.github', 'node_modules', 'tests', 'feature new', '.agents', '__pycache__', '.gemini'}
    exclude_files = {'.gitignore', 'package.json', 'package-lock.json', 'zip_plugin.py', zip_name}
    
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk('.'):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
            
            for file in files:
                if file in exclude_files or file.endswith('.pyc') or file.startswith('.'):
                    continue
                    
                file_path = os.path.join(root, file)
                # Archive name should be relative to the root
                archive_name = os.path.relpath(file_path, '.')
                zipf.write(file_path, archive_name)
    
    print(f"Successfully built {zip_name}")

if __name__ == "__main__":
    build_zip()
