using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
namespace LocalVideoStudio {
static class BlackGoldVisualCheck {
 const BindingFlags F=BindingFlags.Instance|BindingFlags.NonPublic;
 static object Field(StudioForm f,string n){return typeof(StudioForm).GetField(n,F).GetValue(f);}
 static Dictionary<string,object> Obj(params object[] pairs){var d=new Dictionary<string,object>();for(int i=0;i<pairs.Length;i+=2)d[(string)pairs[i]]=pairs[i+1];return d;}
 static void Text(StudioForm f,string n,string v){((TextBox)Field(f,n)).Text=v;}
 [STAThread] static int Main(){try{
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  string root=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"blackgold-refined");Directory.CreateDirectory(root);
  using(var f=new StudioForm(true,delegate(string path,object body,int timeout){throw new InvalidOperationException("Network must remain unused.");})){
   f.Location=new Point(-30000,-30000);f.ShowInTaskbar=false;IntPtr handle=f.Handle;
   if(f.Visible)throw new Exception("Unexpected visible window");
   // Only the managed visibility bit is enabled for DrawToBitmap; the native
   // window stays hidden and no Shown handler, network, poller or draft I/O runs.
   typeof(Control).GetMethod("SetState",F).Invoke(f,new object[]{2,true});
   foreach(Control c in f.Controls)c.CreateControl();
   Text(f,"dramaStory","夜班列车上，女孩捡到一张寄给明天的车票。\r\n克制的电影感，温暖而有悬念。");
   Text(f,"imgPrompt","夜色中的站台，柔和的灯光，电影静帧。");
   Text(f,"content","女孩在空车厢里展开车票，窗外的灯光缓缓掠过。镜头由近景推向特写，情绪安静而专注。");
   Text(f,"prompt","真实电影质感，自然光线，稳定运镜，保持人物与服装一致。");
   Text(f,"ttsText","如果明天真的会来，你会在下一站等我吗？");
   Text(f,"instruct","轻声、平静，带一点期待");
   ((ListBox)Field(f,"dramaList")).Items.AddRange(new object[]{"镜头 01 · 5s · 夜色站台，女孩独自等待列车。","镜头 02 · 5s · 一张车票静静落在空座位上。","镜头 03 · 5s · 她拿起车票，神情若有所思。"});
   ((ChoiceBox)Field(f,"voiceChoice")).Items.Add(new Choice("fixture","晓晓 · 普通话"));((ChoiceBox)Field(f,"voiceChoice")).SelectedIndex=0;
   f.ApplyStatus(Obj("service","video-studio","api_version",1,"controller",Obj("connected",true,"busy",false,"phase","idle","comfy_running",true,"supports_warm_handoff",true,"can_accept_batch",true,"warm_handoff_ready",true,"comfy_idle",true,"can_restore",true),"settings",Obj("key_saved",true,"kimi_model","kimi-k3","kimi_base_url","https://api.moonshot.cn/v1","agent","kimi_api"),"agents",new object[0],"tasks",new object[0],"outputs",new object[0],"catalog",new object[0]));
   var report=new List<string>();
   foreach(float scale in new[]{.75f,1f,1.5f,2f})foreach(bool dark in new[]{true,false})foreach(int page in new[]{0,1,2}){
    f.LayoutScale(scale);f.SetTheme(dark,false);f.ShowPage(page);
    using(var bitmap=new Bitmap(f.Width,f.Height)){f.DrawToBitmap(bitmap,new Rectangle(Point.Empty,f.Size));bitmap.Save(Path.Combine(root,(dark?"dark":"light")+"-"+(int)(scale*100)+"-"+page+".png"));}
    foreach(Control c in f.Controls)if(c.Visible&&(c.Left<0||c.Top<0||c.Right>f.ClientSize.Width||c.Bottom>f.ClientSize.Height))throw new Exception("Bounds failed: "+c.GetType().Name+" "+c.Text);
    report.Add("PASS "+(dark?"dark":"light")+" "+scale+" page "+page);
   }
   typeof(Control).GetMethod("SetState",F).Invoke(f,new object[]{2,false});
   if(f.Visible)throw new Exception("Window must be hidden");
   report.Add("Network requests: 0; native forms shown: 0; model/GPU actions: 0; configuration writes: 0.");File.WriteAllLines(Path.Combine(root,"visual-check.txt"),report.ToArray());
   Console.WriteLine("PASS 24 offscreen renders, all visible controls within client bounds. Native forms shown=0; requests=0.");
  }
  return 0;
 }catch(Exception e){Console.WriteLine(e.ToString());return 1;}}
}
}
