using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace LocalVideoStudio {
// Shared charcoal / champagne palette. Gold is reserved for actions and selection.
internal static class StudioPalette {
 internal static readonly Color Background=Color.FromArgb(16,17,19), Panel=Color.FromArgb(24,26,29), Raised=Color.FromArgb(32,34,38), Line=Color.FromArgb(48,50,56);
 internal static readonly Color Gold=Color.FromArgb(200,170,114), GoldHover=Color.FromArgb(217,190,139), GoldLine=Color.FromArgb(81,70,50);
 internal static readonly Color Ink=Color.FromArgb(238,236,230), Muted=Color.FromArgb(151,149,143), Subtle=Color.FromArgb(104,106,112), Success=Color.FromArgb(129,171,146), Error=Color.FromArgb(213,138,130);
 internal static readonly Color Selection=Color.FromArgb(41,38,32);
}
static class Json {
 internal static JavaScriptSerializer Serializer { get { return new JavaScriptSerializer { MaxJsonLength = 8000000 }; } }
 internal static Dictionary<string,object> Obj(object o) { return o as Dictionary<string,object> ?? new Dictionary<string,object>(); }
 internal static object Get(object o,string k) { object v; return Obj(o).TryGetValue(k,out v)?v:null; }
 internal static string Str(object o,string k,string d="") { return Convert.ToString(Get(o,k)??d); }
 internal static bool Yes(object o,string k) { return Get(o,k) is bool && (bool)Get(o,k); }
 internal static IEnumerable Items(object o) { return o as IEnumerable ?? new object[0]; }
}
static class Api {
 static readonly string backendRoot=ConfigPath("VIDEO_STUDIO_BACKEND",Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"..","video-stack"));
 static readonly string comfyRoot=ConfigPath("VIDEO_STUDIO_COMFYUI",Path.Combine(backendRoot,"..","ComfyUI"));
 static readonly string pythonPath=ConfigValue("VIDEO_STUDIO_PYTHON","python");
 static readonly string voiceOutputRoot=ConfigPath("VIDEO_STUDIO_VOICE_OUTPUT",Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"VideoStudio","voice"));
 internal static string BackendRoot { get { return backendRoot; } }
 internal static string ComfyRoot { get { return comfyRoot; } }
 internal static string PythonPath { get { return pythonPath; } }
 internal static string OutputRoot { get { return Path.Combine(comfyRoot,"output"); } }
 internal static string VoiceOutputRoot { get { return voiceOutputRoot; } }
 static string ConfigPath(string name,string fallback) {
  string value=Environment.GetEnvironmentVariable(name);
  if(String.IsNullOrWhiteSpace(value))value=fallback;
  try { return Path.GetFullPath(value.Trim()); } catch { return Path.GetFullPath(fallback); }
 }
 static string ConfigValue(string name,string fallback) {
  string value=Environment.GetEnvironmentVariable(name);
  return String.IsNullOrWhiteSpace(value)?fallback:value.Trim();
 }
 static DateTime lastStart = DateTime.MinValue;
 internal static Dictionary<string,object> Request(string path, object body=null,int timeout=12000) {
   var req=(HttpWebRequest)WebRequest.Create("http://127.0.0.1:8900"+path);
   req.Proxy=null; req.Timeout=timeout; req.ReadWriteTimeout=timeout; req.Headers["X-Video-Studio"]="1";
   if(body!=null) { req.Method="POST"; req.ContentType="application/json"; byte[] bytes=Encoding.UTF8.GetBytes(Json.Serializer.Serialize(body)); req.ContentLength=bytes.Length; using(var stream=req.GetRequestStream()) stream.Write(bytes,0,bytes.Length); }
   try { using(var response=req.GetResponse()) using(var reader=new StreamReader(response.GetResponseStream(),Encoding.UTF8)) return Json.Obj(Json.Serializer.DeserializeObject(reader.ReadToEnd())); }
   catch(WebException e) { if(e.Response!=null) { using(var reader=new StreamReader(e.Response.GetResponseStream())) { string raw=reader.ReadToEnd(); try { var err=Json.Serializer.DeserializeObject(raw); throw new InvalidOperationException(Json.Str(err,"error",Json.Str(err,"detail","操作未完成，请检查连接。"))); } catch(ArgumentException) { throw new InvalidOperationException("服务返回了无法识别的响应。"); } } } throw; }
 }
 internal static void EnsureService() {
   if((DateTime.UtcNow-lastStart).TotalSeconds<25)return;
   lastStart=DateTime.UtcNow;
   Process.Start(new ProcessStartInfo(PythonPath,"-X utf8 -u \""+BackendRoot+"\\studio_service.py\"") { WorkingDirectory=BackendRoot,UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden });
 }
}
class QuietButton : Button {
 internal bool Dark, Primary, Selected;
 internal Color Surface;
 bool hover;
 internal float ScaleValue=1;
 internal QuietButton() { SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true); FlatStyle=FlatStyle.Flat; FlatAppearance.BorderSize=0; Cursor=Cursors.Hand; TabStop=true; }
 protected override void OnMouseEnter(EventArgs e){hover=true;Invalidate();base.OnMouseEnter(e);}
 protected override void OnMouseLeave(EventArgs e){hover=false;Invalidate();base.OnMouseLeave(e);}
 protected override void OnPaint(PaintEventArgs e) {
  e.Graphics.Clear(Surface.IsEmpty?Parent.BackColor:Surface); e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;
  Color fill=Primary?(Dark?StudioPalette.Gold:Color.FromArgb(196,110,84)):(Selected?(Dark?StudioPalette.Selection:Color.FromArgb(228,235,229)):(Dark?StudioPalette.Raised:Color.FromArgb(240,242,240)));
  Color ink=Primary?(Dark?StudioPalette.Background:Color.White):(Selected&&Dark?StudioPalette.Gold:Dark?StudioPalette.Ink:Color.FromArgb(38,43,39));
  if(hover&&Enabled)fill=Dark?(Primary?StudioPalette.GoldHover:StudioPalette.Line):ControlPaint.Light(fill,.08f);
  if(!Enabled){fill=Dark?StudioPalette.Panel:Color.FromArgb(235,237,234);ink=Dark?StudioPalette.Subtle:Color.FromArgb(154,161,155);}
  using(var path=StudioForm.Rounded(new RectangleF(1,1,Width-2,Height-2),8*ScaleValue)) {
   using(var b=new SolidBrush(fill))e.Graphics.FillPath(b,path);
   if(Selected&&Enabled)using(var pen=new Pen(Dark?StudioPalette.GoldLine:Color.FromArgb(184,201,187)))e.Graphics.DrawPath(pen,path);
  }
  TextRenderer.DrawText(e.Graphics,Text,Font,ClientRectangle,ink,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
  if(Focused&&ShowFocusCues)ControlPaint.DrawFocusRectangle(e.Graphics,Rectangle.Inflate(ClientRectangle,-5,-5),ink,fill);
 }
}
internal sealed class GenerationProgressState {
 internal string Title="生成进度 · 待提交",Detail="提交后显示实际生成进度",Stage="idle";
 internal int Percent;
 internal bool Indeterminate,Failed;
 static bool Number(object value,out double number){
  number=0;if(!(value is byte||value is short||value is int||value is long||value is float||value is double||value is decimal))return false;
  try{number=Convert.ToDouble(value);return !Double.IsNaN(number)&&!Double.IsInfinity(number);}catch{return false;}
 }
 internal static GenerationProgressState From(object control,bool connected,bool preparing){
  var view=new GenerationProgressState();
  if(!connected||!Json.Yes(control,"connected")){view.Title="生成进度 · 连接中";view.Detail="正在连接进度服务；已提交的任务仍在后台运行。";return view;}
  bool busy=Json.Yes(control,"busy");string phase=Json.Str(control,"phase");object progress=Json.Get(control,"progress");
  if(!preparing&&(phase=="failed"||phase=="interrupted")){
   view.Stage=phase;view.Failed=true;view.Detail=Json.Str(control,"detail").Trim();if(view.Detail.Length==0)view.Detail="生成已停止，请查看任务状态。";if(view.Detail.Length>240)view.Detail=view.Detail.Substring(0,240);view.Title=phase=="interrupted"?"生成进度 · 已中断":view.Detail.Contains("显存不足")?"生成进度 · 显存不足":"生成进度 · 失败";return view;
  }
  if(Json.Get(control,"busy") is bool&&!busy&&!preparing&&phase=="batch_done"){
   view.Stage="done";view.Percent=100;view.Title="生成进度 · 100%";view.Detail="视频已生成并通过 MP4 核验。";return view;
  }
  if(!busy&&!preparing)return view;
  view.Indeterminate=true;view.Stage=preparing&&!busy?"preparing":phase;
  view.Title="生成进度 · 处理中";
  switch(view.Stage){
   case "queued":view.Detail="任务已接收，等待 GPU 交接";break;
   case "checking_comfy":view.Detail="正在检查视频引擎";break;
   case "stopping_strata":view.Detail="正在释放显存";break;
   case "starting_comfy":view.Detail="正在启动视频引擎";break;
   case "queueing":view.Detail="正在提交视频任务";break;
   case "preparing":view.Detail="正在准备工作流";break;
   case "restoring":view.Detail="正在恢复 Strata";break;
   default:view.Detail="正在生成，等待引擎报告实际进度";break;
  }
  if(busy&&Json.Yes(progress,"active")){
   view.Stage=Json.Str(progress,"stage",view.Stage);string label=Json.Str(progress,"label");if(!String.IsNullOrWhiteSpace(label))view.Detail=label.Length>160?label.Substring(0,160):label;
   double value,max;
   if(view.Stage=="sampling"&&Json.Get(progress,"indeterminate") is bool&&!(bool)Json.Get(progress,"indeterminate")&&Number(Json.Get(progress,"value"),out value)&&Number(Json.Get(progress,"max"),out max)&&max>0&&value>=0&&value<max){
    view.Percent=(int)Math.Max(0,Math.Min(99,Math.Floor(value/max*100)));view.Indeterminate=false;view.Title="生成进度 · 采样 "+view.Percent+"%";
   }
   double index,count,elapsed;string meta="";
   if(Number(Json.Get(progress,"task_index"),out index)&&Number(Json.Get(progress,"task_count"),out count)&&index>=1&&count>=index)meta="第 "+((int)Math.Min(index,9999))+" / "+((int)Math.Min(count,9999))+" 条";
   if(Number(Json.Get(progress,"elapsed_seconds"),out elapsed)&&elapsed>=0){var span=TimeSpan.FromSeconds(Math.Min(elapsed,31536000));string clock=span.TotalHours>=1?((int)span.TotalHours)+":"+span.Minutes.ToString("00")+":"+span.Seconds.ToString("00"):span.Minutes.ToString("00")+":"+span.Seconds.ToString("00");meta+=(meta.Length>0?" · ":"")+"已用 "+clock;}
   if(meta.Length>0)view.Detail+="\n"+meta;
  }
  return view;
 }
}
internal sealed class GenerationMeter : Control {
 internal bool Dark=true;
 readonly Timer animation=new Timer{Interval=60};
 int percent,tick;bool indeterminate,failed;
 internal GenerationMeter(){SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true);TabStop=false;AccessibleRole=AccessibleRole.ProgressBar;AccessibleName="视频生成进度";animation.Tick+=delegate{tick=(tick+1)%80;Invalidate();};}
 internal void SetProgress(GenerationProgressState state){percent=Math.Max(0,Math.Min(100,state.Percent));indeterminate=state.Indeterminate;failed=state.Failed;AccessibleDescription=state.Title+"，"+state.Detail;UpdateAnimation();Invalidate();}
 void UpdateAnimation(){if(indeterminate&&Visible&&IsHandleCreated&&!IsDisposed)animation.Start();else animation.Stop();}
 protected override void OnVisibleChanged(EventArgs e){base.OnVisibleChanged(e);UpdateAnimation();}
 protected override void OnHandleCreated(EventArgs e){base.OnHandleCreated(e);UpdateAnimation();}
 protected override void OnHandleDestroyed(EventArgs e){animation.Stop();base.OnHandleDestroyed(e);}
 protected override void OnPaint(PaintEventArgs e){
  e.Graphics.Clear(BackColor);e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;
  var track=new RectangleF(0,0,Math.Max(1,Width-1),Math.Max(1,Height-1));float radius=Math.Max(1,track.Height/2);
  using(var path=StudioForm.Rounded(track,radius))using(var brush=new SolidBrush(Dark?StudioPalette.Raised:Color.FromArgb(222,230,223))){e.Graphics.FillPath(brush,path);e.Graphics.SetClip(path);float width=indeterminate?Math.Max(24,Width*.23f):Width*percent/100f;float x=indeterminate?(Width+width)*tick/79f-width:0;if(width>0)using(var fill=new SolidBrush(failed?StudioPalette.Error:Dark?StudioPalette.Gold:Color.FromArgb(196,110,84)))e.Graphics.FillRectangle(fill,x,0,width,Height);e.Graphics.ResetClip();}
 }
 protected override void Dispose(bool disposing){if(disposing)animation.Dispose();base.Dispose(disposing);}
}
class Choice { internal string Id,Label; internal Choice(string id,string label){Id=id;Label=label;} public override string ToString(){return Label;} }
class ToggleSwitch : Control {
 internal bool Dark;internal float ScaleValue=1;
 bool on,hover;float thumbT;readonly Timer anim=new Timer{Interval=16};
 internal event EventHandler CheckedChanged;
 internal bool Checked{get{return on;}set{if(value==on)return;on=value;anim.Stop();anim.Start();if(CheckedChanged!=null)CheckedChanged(this,EventArgs.Empty);}}
 internal ToggleSwitch(){SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.Selectable,true);TabStop=true;Cursor=Cursors.Hand;anim.Tick+=delegate{float target=on?1f:0f;thumbT+=(target-thumbT)*0.35f;if(Math.Abs(target-thumbT)<0.02f){thumbT=target;anim.Stop();}Invalidate();};}
 internal void Set(bool value){on=value;thumbT=value?1f:0f;Invalidate();}
 protected override void OnClick(EventArgs e){Checked=!on;base.OnClick(e);}
 protected override void OnKeyDown(KeyEventArgs e){if(e.KeyCode==Keys.Space||e.KeyCode==Keys.Enter){Checked=!on;e.Handled=true;}base.OnKeyDown(e);}
 protected override void OnMouseEnter(EventArgs e){hover=true;Invalidate();base.OnMouseEnter(e);}
 protected override void OnMouseLeave(EventArgs e){hover=false;Invalidate();base.OnMouseLeave(e);}
 protected override void OnPaint(PaintEventArgs e){var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;float s=ScaleValue;var track=new RectangleF(1,1,Width-2,Height-2);Color c=on?(Dark?StudioPalette.Gold:Color.FromArgb(196,110,84)):(Dark?StudioPalette.Line:Color.FromArgb(198,206,198));if(hover&&Enabled)c=ControlPaint.Light(c,.08f);using(var p=StudioForm.Rounded(track,(Height-2)/2f))using(var b=new SolidBrush(c))g.FillPath(b,p);float thumb=Height-8*s;float x=4*s+(Width-8*s-thumb)*thumbT;Color tc=!Enabled?StudioPalette.Subtle:(Dark?(on?StudioPalette.Background:StudioPalette.Ink):Color.White);using(var b=new SolidBrush(tc))g.FillEllipse(b,x,4*s,thumb,thumb);if(!Enabled)using(var p=StudioForm.Rounded(track,(Height-2)/2f))using(var b=new SolidBrush(Color.FromArgb(90,Dark?Color.Black:Color.White)))g.FillPath(b,p);}
 protected override void Dispose(bool disposing){if(disposing)anim.Dispose();base.Dispose(disposing);}
}
sealed class ChoiceMenuColors : ProfessionalColorTable {
 readonly bool dark;
 internal ChoiceMenuColors(bool value){dark=value;UseSystemColors=false;}
 public override Color ToolStripDropDownBackground {get{return dark?StudioPalette.Panel:Color.White;}}
 public override Color MenuBorder {get{return dark?StudioPalette.Line:Color.FromArgb(207,219,203);}}
 public override Color MenuItemBorder {get{return dark?StudioPalette.GoldLine:Color.FromArgb(184,201,187);}}
 public override Color MenuItemSelected {get{return dark?StudioPalette.Selection:Color.FromArgb(234,240,234);}}
 public override Color MenuItemSelectedGradientBegin {get{return MenuItemSelected;}}
 public override Color MenuItemSelectedGradientEnd {get{return MenuItemSelected;}}
 public override Color MenuItemPressedGradientBegin {get{return MenuItemSelected;}}
 public override Color MenuItemPressedGradientEnd {get{return MenuItemSelected;}}
 public override Color MenuItemPressedGradientMiddle {get{return MenuItemSelected;}}
 public override Color CheckBackground {get{return MenuItemSelected;}}
 public override Color CheckSelectedBackground {get{return MenuItemSelected;}}
 public override Color CheckPressedBackground {get{return MenuItemSelected;}}
 public override Color SeparatorDark {get{return MenuBorder;}}
 public override Color SeparatorLight {get{return ToolStripDropDownBackground;}}
}
class ChoiceBox : Control {
 internal readonly List<object> Items=new List<object>();
 internal bool Dark;
 int selected=-1;
 ContextMenuStrip openMenu;
 bool disposingMenu;
 internal event EventHandler SelectedIndexChanged;
 internal int SelectedIndex {get{return selected;}set{if(value==selected)return;selected=value;Invalidate();if(SelectedIndexChanged!=null)SelectedIndexChanged(this,EventArgs.Empty);}}
 internal object SelectedItem {get{return selected>=0&&selected<Items.Count?Items[selected]:null;}}
 internal ChoiceBox(){SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.Selectable,true);TabStop=true;Cursor=Cursors.Hand;AccessibleRole=AccessibleRole.ComboBox;openMenu=new ContextMenuStrip{ShowImageMargin=false};}
 protected override void OnClick(EventArgs e){Focus();OpenMenu();base.OnClick(e);}
 void OpenMenu(){
  if(IsDisposed||Disposing||disposingMenu||!Enabled||!IsHandleCreated||openMenu==null||openMenu.IsDisposed||openMenu.Visible)return;
  var menu=openMenu;
  // Rebuild only while hidden. Closing a popup must not destroy its native
  // handle while WinForms' modal menu filter is still unwinding.
  menu.BackColor=BackColor;menu.ForeColor=ForeColor;menu.Font=Font;menu.Renderer=new ToolStripProfessionalRenderer(new ChoiceMenuColors(Dark)){RoundedEdges=false};
  while(menu.Items.Count>0){var old=menu.Items[menu.Items.Count-1];menu.Items.RemoveAt(menu.Items.Count-1);old.Dispose();}
  for(int i=0;i<Items.Count;i++){int n=i;var item=new ToolStripMenuItem(Convert.ToString(Items[i])){Checked=i==selected,AutoSize=true};item.Click+=delegate{SelectedIndex=n;};menu.Items.Add(item);}
  if(menu.Items.Count>0)menu.Show(this,new Point(0,Height));
 }
 protected override void Dispose(bool disposing){
  if(disposing&&!disposingMenu){
   disposingMenu=true;var menu=openMenu;openMenu=null;
   try{if(menu!=null&&!menu.IsDisposed){try{menu.Close(ToolStripDropDownCloseReason.CloseCalled);}finally{menu.Dispose();}}}
   finally{base.Dispose(disposing);}
   return;
  }
  base.Dispose(disposing);
 }
 protected override void OnKeyDown(KeyEventArgs e){if(e.KeyCode==Keys.Space||e.KeyCode==Keys.Enter){OpenMenu();e.Handled=true;}else if(e.KeyCode==Keys.Down){SelectedIndex=Math.Min(Items.Count-1,selected+1);e.Handled=true;}else if(e.KeyCode==Keys.Up){SelectedIndex=Math.Max(0,selected-1);e.Handled=true;}base.OnKeyDown(e);}
 protected override bool IsInputKey(Keys keyData){return keyData==Keys.Up||keyData==Keys.Down||base.IsInputKey(keyData);}
 protected override void OnPaint(PaintEventArgs e){e.Graphics.Clear(BackColor);float s=Height/34f;e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;using(var p=StudioForm.Rounded(new RectangleF(0,0,Width-1,Height-1),8*s))using(var pen=new Pen(Dark?(Focused?StudioPalette.GoldLine:StudioPalette.Line):Color.FromArgb(207,219,203)))e.Graphics.DrawPath(pen,p);TextRenderer.DrawText(e.Graphics,SelectedItem==null?"":SelectedItem.ToString(),Font,new Rectangle((int)(10*s),0,Width-(int)(36*s),Height),ForeColor,TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);using(var pen=new Pen(Dark?StudioPalette.Muted:ForeColor,1.2f*s)){float x=Width-18*s,y=Height/2-2*s;e.Graphics.DrawLines(pen,new[]{new PointF(x-4*s,y),new PointF(x,y+4*s),new PointF(x+4*s,y)});}if(Focused&&ShowFocusCues)ControlPaint.DrawFocusRectangle(e.Graphics,Rectangle.Inflate(ClientRectangle,-3,-3));}
}

}
