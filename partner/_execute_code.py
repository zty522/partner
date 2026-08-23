import sys; sys.path.insert(0, '/mnt/e/work/partner')
import sys
sys.path.insert(0, '/mnt/e/work/partner/partner')

# Create a small real PDF file (192 bytes)
small_pdf = b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF'
with open('/tmp/small_test.pdf', 'wb') as f:
    f.write(small_pdf)

# Create a fake PDF (md content, 2000 bytes)
fake_pdf = b'# Fake PDF\n\nThis is markdown content pretending to be a PDF.\n' * 100
with open('/tmp/fake_test.pdf', 'wb') as f:
    f.write(fake_pdf)

print(f'small_test.pdf size: {len(small_pdf)} bytes')
print(f'fake_test.pdf size: {len(fake_pdf)} bytes')

# Import and test validate_pdf
from __main__ import validate_pdf

# Test 1: small real PDF should pass
result1 = validate_pdf('/tmp/small_test.pdf')
print(f'Small PDF result: {result1}')
assert result1[0] == True, f'Small PDF should be valid, got: {result1}'

# Test 2: fake PDF should be rejected
result2 = validate_pdf('/tmp/fake_test.pdf')
print(f'Fake PDF result: {result2}')
assert result2[0] == False, f'Fake PDF should be invalid, got: {result2}'

print('\nAll tests passed!')