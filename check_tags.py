import re
import sys

def check_tags(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Simple tag parser
    tags = re.findall(r'</?([a-zA-Z0-9_.-]+)[^>]*>', content)
    
    stack = []
    lines = content.split('\n')
    
    for i, line in enumerate(lines):
        # find all tags in line
        matches = re.finditer(r'<(/)?([a-zA-Z0-9_.-]+)([^>]*)>', line)
        for match in matches:
            is_close = match.group(1) == '/'
            tag_name = match.group(2)
            attributes = match.group(3)
            
            # Skip self closing
            if attributes.strip().endswith('/'):
                continue
                
            # Skip known self closing HTML tags
            if tag_name.lower() in ['input', 'br', 'hr', 'img', 'meta', 'link']:
                continue
                
            if is_close:
                if stack and stack[-1][0] == tag_name:
                    stack.pop()
                else:
                    print(f"Mismatch at line {i+1}: expected closing for {stack[-1] if stack else 'None'}, found </{tag_name}>")
            else:
                stack.append((tag_name, i+1))
                
    if stack:
        print("Unclosed tags at EOF:")
        for tag, line in stack:
            print(f"  <{tag}> from line {line}")
    else:
        print("Tags perfectly balanced!")

check_tags(sys.argv[1])
