import pdfplumber, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

pdf_path = 'C:/Users/rober/.claude/projects/c--Users-rober-Desktop-ScratchFever/8f6248cc-55f2-4f29-9997-3cca5cd8772a/tool-results/webfetch-1778810422387-n08kju.pdf'
with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        print(f'=== PAGE {i+1} ===')
        text = page.extract_text()
        print(repr(text))
        print()
        tables = page.extract_tables()
        for j, tbl in enumerate(tables):
            print(f'  TABLE {j+1}:')
            for row in tbl:
                print('   ', row)
        print()

    full_text = ''
    for page in pdf.pages:
        full_text += (page.extract_text() or '') + '\n'

    print('=== REGEX MATCHES (current pattern) ===')
    pattern = r'\$([\d,]+(?:\.\d+)?)\s*=\s*([\d,]+(?:\.\d+)?)'
    matches = re.findall(pattern, full_text)
    print(matches)

    print()
    print('=== ALL LINES WITH $ ===')
    for line in full_text.split('\n'):
        if '$' in line:
            print(repr(line))
