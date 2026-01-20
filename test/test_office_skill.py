import os
import sys
import shutil
import importlib.util

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load module dynamically because of hyphen in name
spec = importlib.util.spec_from_file_location("impl", os.path.join(os.path.dirname(__file__), '../skills/office-suite/impl.py'))
impl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impl)

def test_office_skill():
    workspace_dir = os.path.abspath("test_workspace")
    if os.path.exists(workspace_dir):
        shutil.rmtree(workspace_dir)
    os.makedirs(workspace_dir)
    
    print(f"Testing in {workspace_dir}")
    
    # 1. DOCX
    print("Testing DOCX...")
    res = impl.write_docx(workspace_dir, "test.docx", "Hello World\nThis is a test.")
    print(f"Write DOCX: {res}")
    content = impl.read_docx(workspace_dir, "test.docx")
    print(f"Read DOCX: {content}")
    assert "Hello World" in content
    
    # 2. PPTX
    print("\nTesting PPTX...")
    slides = [{"title": "Title 1", "content": "Content 1"}, {"title": "Title 2", "content": "Content 2"}]
    res = impl.create_pptx(workspace_dir, "test.pptx", slides)
    print(f"Create PPTX: {res}")
    content = impl.read_pptx(workspace_dir, "test.pptx")
    print(f"Read PPTX: {content}")
    assert "Title 1" in content
    
    # 3. Excel
    print("\nTesting Excel...")
    data = [["Name", "Age"], ["Alice", 30], ["Bob", 25]]
    res = impl.write_excel(workspace_dir, "test.xlsx", data)
    print(f"Write Excel: {res}")
    content = impl.read_excel(workspace_dir, "test.xlsx")
    print(f"Read Excel: \n{content}")
    assert "Alice" in content
    
    # Cleanup
    # shutil.rmtree(workspace_dir)
    print("\nTest Complete.")

if __name__ == "__main__":
    test_office_skill()
