from pathlib import Path
import hashlib, shutil, subprocess
from PIL import Image, ImageChops, ImageFont
root=Path('AGENT_WORK/CODEX_EVIDENCE/ttc-probe').resolve()
fonts=root/'fonts'; fonts.mkdir(parents=True,exist_ok=True)
font=Path('C:/Windows/Fonts/Nirmala.ttc'); shutil.copy2(font, fonts/font.name)
ff='D:/ffmpeg/bin/ffmpeg.exe'
(root/'subs.ass').write_text('[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Nirmala UI,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,20,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.20,0:00:02.80,Default,,0,0,0,,இந்திய இராணுவம் புதிய விமானங்களை வாங்கியது\n',encoding='utf-8')
subprocess.run([ff,'-hide_banner','-v','error','-y','-f','lavfi','-i','color=c=0x203040:s=640x360:d=3:r=25','-f','lavfi','-i','anullsrc=r=48000:cl=mono','-t','3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(root/'in.mp4')],check=True)
cmd=[ff,'-hide_banner','-nostdin','-y','-i','in.mp4','-vf','ass=subs.ass:fontsdir=fonts','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart','out.mp4']
p=subprocess.run(cmd,cwd=root,capture_output=True,text=True)
(root/'ffmpeg.stderr.log').write_text(p.stderr,encoding='utf-8'); print('ffmpeg exit',p.returncode); print('\n'.join(x for x in p.stderr.splitlines() if 'font' in x.lower() or 'ass' in x.lower())[-3000:])
if p.returncode: raise SystemExit(p.returncode)
for video,out in [('in.mp4','plain.png'),('out.mp4','burned.png')]:
    subprocess.run([ff,'-v','error','-y','-ss','1','-i',video,'-frames:v','1',out],cwd=root,check=True)
a=Image.open(root/'plain.png').convert('L'); b=Image.open(root/'burned.png').convert('L')
bbox=ImageChops.difference(a,b).point(lambda x:255 if x>40 else 0).getbbox()
print('rendered_pixel_bbox',bbox,'file',root/'burned.png','sha256',hashlib.sha256((root/'burned.png').read_bytes()).hexdigest())
f=ImageFont.truetype(str(font),48,index=0); missing=f.getmask('\U0010ffff')
text='தமிழ் செய்தி இந்தியா'
missing_chars=[ch for ch in sorted(set(text.replace(' ',''))) if f.getmask(ch).size==missing.size and f.getmask(ch).tobytes()==missing.tobytes()]
print('Pillow face',f.getname(),'Raqm disabled; per-codepoint notdef matches:',repr(missing_chars),'sample:',repr(text))
