from pathlib import Path
p=Path('.tmp/quality-frontend/admin-page.tsx')
s=p.read_text(encoding='utf-8')
s=s.replace('useEffect, useLayoutEffect, useMemo','useEffect, useMemo').replace('ApiError, apiUrl','ApiError')
s=s.replace('  const [svgError, setSvgError] = useState(false);\n','').replace('!svgError && store.logo_svg','store.logo_svg')
s=s.replace('const [includeImages, setIncludeImages] = useState(false);','const [includeImagesOverride, setIncludeImagesOverride] = useState<boolean | null>(null);')
s=s.replace('  const [includeImagesTouched, setIncludeImagesTouched] = useState(false);\n','')
s=s.replace('  useEffect(() => {\n    if (includeImagesTouched) return;\n    setIncludeImages(defaultIncludeImagesForStore(matchedStore));\n  }, [matchedStore, includeImagesTouched]);','  const includeImages = includeImagesOverride ?? defaultIncludeImagesForStore(matchedStore);')
s=s.replace('setIncludeImagesTouched(false);','setIncludeImagesOverride(null);').replace('setIncludeImagesTouched(true); setIncludeImages(value);','setIncludeImagesOverride(value);')
p.write_text(s,encoding='utf-8')
p=Path('.tmp/quality-frontend/services.ts')
s=p.read_text(encoding='utf-8')
for signature,arg in [('async startGoogleLogin(_nextPath?: string): Promise<string> {','_nextPath'),('signInUrl(_nextPath: string): string | null {','_nextPath'),('async getPreview(_previewId: string): Promise<ProductPreviewResponse> {','_previewId'),('): Promise<{ product: CatalogProduct; message?: string }> {','_previewId')]:
    assert signature in s,signature
    s=s.replace(signature,signature+'\n    void '+arg+';')
p.write_text(s,encoding='utf-8')
