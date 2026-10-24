from pathlib import Path
from PIL import Image, ImageDraw
root=Path(__file__).resolve().parent
s=1024
im=Image.new('RGBA',(s,s),(0,0,0,0))
d=ImageDraw.Draw(im)
d.rounded_rectangle((18,18,1006,1006),radius=226,fill='#181e19')
ink='#f1f4eb'
for points in [((250,408),(250,276),(388,276)),((636,276),(774,276),(774,408)),((250,616),(250,748),(388,748)),((636,748),(774,748),(774,616))]:
    d.line(points,fill=ink,width=40,joint='curve')
d.polygon([(458,398),(458,626),(638,512)],fill='#b5cd9c')
im.resize((512,512),Image.Resampling.LANCZOS).save(root/'studio.png')
im.save(root/'studio.ico',sizes=[(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(128,128),(256,256)])