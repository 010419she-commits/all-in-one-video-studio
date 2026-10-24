// Offline native progress regression. Compile all five production sources with
// /define:GENERATION_PROGRESS_CHECK /main:LocalVideoStudio.GenerationProgressCheck.
// Creates a hidden form only. No Show, submit action, networking or real draft I/O.
#if GENERATION_PROGRESS_CHECK
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Reflection;
using System.Windows.Forms;

namespace LocalVideoStudio {
static class GenerationProgressCheck {
 sealed class Case {
  internal string Name,Stage,TitlePart;
  internal object Control;
  internal int Percent;
  internal bool Connected=true,Preparing,Indeterminate,Failed;
 }
 static int assertions,requests;
 static void Check(bool value,string name){assertions++;if(!value)throw new InvalidOperationException("FAILED: "+name);}
 static Dictionary<string,object> Control(bool busy,string phase){return new Dictionary<string,object>{{"connected",true},{"busy",busy},{"phase",phase},{"comfy_running",true},{"supports_warm_handoff",true},{"can_accept_batch",!busy},{"warm_handoff_ready",!busy},{"comfy_idle",!busy},{"can_restore",!busy}};}
 static Dictionary<string,object> Progress(string stage,object value,object max){return new Dictionary<string,object>{{"active",true},{"stage",stage},{"value",value},{"max",max},{"indeterminate",false},{"label","Fixture "+stage},{"task_index",2},{"task_count",3},{"elapsed_seconds",65}};}
 static Dictionary<string,object> With(Dictionary<string,object> source,string key,object value){var clone=new Dictionary<string,object>(source);clone[key]=value;return clone;}
 static Dictionary<string,object> Without(Dictionary<string,object> source,string key){var clone=new Dictionary<string,object>(source);clone.Remove(key);return clone;}
 static Dictionary<string,object> Sampling(object value,object max){return With(Control(true,"generating"),"progress",Progress("sampling",value,max));}
 static void Add(List<Case> cases,string name,object control,string stage,int percent,bool indeterminate,bool failed,string titlePart){cases.Add(new Case{Name=name,Control=control,Stage=stage,Percent=percent,Indeterminate=indeterminate,Failed=failed,TitlePart=titlePart});}
 static List<Case> Cases(){
  var cases=new List<Case>();
  Add(cases,"ready idle",Control(false,"idle"),"idle",0,false,false,"待提交");
  Add(cases,"old controller busy fallback",Control(true,"generating"),"generating",0,true,false,"处理中");
  Add(cases,"verified batch done",Control(false,"batch_done"),"done",100,false,false,"100%");
  Add(cases,"completed phase without boolean busy",Without(Control(false,"batch_done"),"busy"),"idle",0,false,false,"待提交");
  foreach(object malformedBusy in new object[]{null,"false",0})
   Add(cases,"completed phase malformed busy "+(malformedBusy==null?"null":malformedBusy.ToString()+"/"+malformedBusy.GetType().Name),With(Control(false,"batch_done"),"busy",malformedBusy),"idle",0,false,false,"待提交");
  Add(cases,"busy batch_done cannot finish",Control(true,"batch_done"),"batch_done",0,true,false,"处理中");
  Add(cases,"failed",Control(false,"failed"),"failed",0,false,true,"失败");
  Add(cases,"failed while busy",Control(true,"failed"),"failed",0,false,true,"失败");
  Add(cases,"interrupted",Control(false,"interrupted"),"interrupted",0,false,true,"已中断");
  Add(cases,"sampling floor",Sampling(1,3),"sampling",33,false,false,"采样 33%");
  Add(cases,"sampling 99 stays below completion",Sampling(999,1000),"sampling",99,false,false,"采样 99%");
  Add(cases,"sampling zero",Sampling(0,20),"sampling",0,false,false,"采样 0%");
  Add(cases,"sampling decimal",Sampling(1.5m,4m),"sampling",37,false,false,"采样 37%");
  Add(cases,"sampling Int64",Sampling((long)3,(long)8),"sampling",37,false,false,"采样 37%");
  foreach(object value in new object[]{20,21,-1,Double.NaN,Double.PositiveInfinity,Double.NegativeInfinity,true,false,"10",null})
   Add(cases,"bad sample value "+(value==null?"null":value.ToString()+"/"+value.GetType().Name),Sampling(value,20),"sampling",0,true,false,"处理中");
  foreach(object max in new object[]{0,-1,Double.NaN,Double.PositiveInfinity,Double.NegativeInfinity,true,false,"20",null})
   Add(cases,"bad sample max "+(max==null?"null":max.ToString()+"/"+max.GetType().Name),Sampling(2,max),"sampling",0,true,false,"处理中");
  var active=Progress("sampling",10,20);
  foreach(object marker in new object[]{true,"false",0,null})
   Add(cases,"explicit false required "+(marker==null?"null":marker.ToString()+"/"+marker.GetType().Name),With(Control(true,"generating"),"progress",With(active,"indeterminate",marker)),"sampling",0,true,false,"处理中");
  Add(cases,"missing indeterminate marker",With(Control(true,"generating"),"progress",Without(active,"indeterminate")),"sampling",0,true,false,"处理中");
  foreach(string field in new[]{"value","max"})
   Add(cases,"missing sampling "+field,With(Control(true,"generating"),"progress",Without(active,field)),"sampling",0,true,false,"处理中");
  Add(cases,"missing progress stage",With(Control(true,"generating"),"progress",Without(active,"stage")),"generating",0,true,false,"处理中");
  Add(cases,"missing progress active",With(Control(true,"generating"),"progress",Without(active,"active")),"generating",0,true,false,"处理中");
  foreach(object marker in new object[]{false,"true",1,null})
   Add(cases,"active exact true "+(marker==null?"null":marker.ToString()+"/"+marker.GetType().Name),With(Control(true,"generating"),"progress",With(active,"active",marker)),"generating",0,true,false,"处理中");
  foreach(string stage in new[]{"loading","preprocess","postprocess","verifying","unknown"})
   Add(cases,"non-sampling stage "+stage,With(Control(true,"generating"),"progress",Progress(stage,10,20)),stage,0,true,false,"处理中");
  Add(cases,"idle ignores stale sample",With(Control(false,"idle"),"progress",active),"idle",0,false,false,"待提交");
  Add(cases,"idle ignores stale full sample",With(Control(false,"idle"),"progress",Progress("sampling",20,20)),"idle",0,false,false,"待提交");
  Add(cases,"controller offline ignores stale sample",With(Sampling(10,20),"connected",false),"idle",0,false,false,"连接中");
  cases.Add(new Case{Name="transport offline ignores stale completed",Control=Control(false,"batch_done"),Connected=false,Stage="idle",TitlePart="连接中"});
  cases.Add(new Case{Name="preparing next after done",Control=Control(false,"batch_done"),Preparing=true,Stage="preparing",Indeterminate=true,TitlePart="处理中"});
  cases.Add(new Case{Name="preparing cold",Control=Control(false,"idle"),Preparing=true,Stage="preparing",Indeterminate=true,TitlePart="处理中"});
  foreach(object malformed in new object[]{null,"bad",new object[0],new Dictionary<string,object>()})
   Add(cases,"malformed controller "+(malformed==null?"null":malformed.GetType().Name),malformed,"idle",0,false,false,"连接中");
  return cases;
 }
 static Dictionary<string,object> Status(object controller){return new Dictionary<string,object>{{"service","video-studio"},{"api_version",1},{"controller",controller},{"settings",new Dictionary<string,object>{{"key_saved",true},{"kimi_base_url","https://api.moonshot.cn/v1"},{"kimi_model","kimi-k3"},{"agent","kimi_api"}}},{"agents",new object[0]},{"tasks",new object[0]},{"outputs",new object[0]},{"catalog",new object[0]}};}
 static object Field(object source,string name){return source.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic).GetValue(source);}
 static void Set(object source,string name,object value){source.GetType().GetField(name,BindingFlags.Instance|BindingFlags.NonPublic).SetValue(source,value);}
 static void Controls(StudioForm form){typeof(StudioForm).GetMethod("SetControls",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(form,null);}
 static void AssertCase(Case c){var actual=GenerationProgressState.From(c.Control,c.Connected,c.Preparing);Check(actual.Stage==c.Stage,c.Name+" stage: "+actual.Stage);Check(actual.Percent==c.Percent,c.Name+" percent: "+actual.Percent);Check(actual.Indeterminate==c.Indeterminate,c.Name+" indeterminate");Check(actual.Failed==c.Failed,c.Name+" failed");Check(actual.Title.Contains(c.TitlePart),c.Name+" title: "+actual.Title);}
 [STAThread] static int Main(){try{
  var cases=Cases();foreach(var c in cases)AssertCase(c);
  var meta=GenerationProgressState.From(Sampling(4,20),true,false);Check(meta.Detail.Contains("第 2 / 3 条")&&meta.Detail.Contains("已用 01:05"),"task and elapsed metadata");
  var hour=With(Progress("sampling",1,20),"elapsed_seconds",3661);Check(GenerationProgressState.From(With(Control(true,"generating"),"progress",hour),true,false).Detail.Contains("1:01:01"),"hour clock metadata");
  foreach(object bad in new object[]{Double.NaN,Double.PositiveInfinity,-1,true,"65",null}){var invalid=With(Progress("loading",0,1),"elapsed_seconds",bad);Check(!GenerationProgressState.From(With(Control(true,"generating"),"progress",invalid),true,false).Detail.Contains("已用"),"invalid elapsed ignored "+bad);}
  var longLabel=With(Progress("loading",0,1),"label",new String('x',300));var labelView=GenerationProgressState.From(With(Control(true,"generating"),"progress",longLabel),true,false);Check(labelView.Detail.Split('\n')[0].Length==160,"oversized backend label bounded");
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  using(var form=new StudioForm(true,delegate(string path,object body,int timeout){requests++;throw new InvalidOperationException("No requests allowed in progress QA");})){
   IntPtr handle=form.Handle;Check(form.IsHandleCreated&&!form.Visible,"hidden form without Show");
   var title=(Label)Field(form,"progressTitle");var detail=(Label)Field(form,"jobDetail");var meter=(GenerationMeter)Field(form,"generationMeter");
   Check(meter.AccessibleRole==AccessibleRole.ProgressBar&&meter.AccessibleName=="视频生成进度","native progress accessibility role/name");
   foreach(var c in cases){
    Set(form,"submittingVideo",c.Preparing);form.ApplyStatus(Status(c.Control));Set(form,"connected",c.Connected);Controls(form);
    Check(title.Text.Contains(c.TitlePart),"actual native title: "+c.Name);Check((int)Field(meter,"percent")==c.Percent,"actual native meter percent: "+c.Name);Check((bool)Field(meter,"indeterminate")==c.Indeterminate,"actual native marquee: "+c.Name);Check((bool)Field(meter,"failed")==c.Failed,"actual native failure: "+c.Name);
    Check(meter.AccessibleDescription==title.Text+"，"+detail.Text,"native accessible progress: "+c.Name);Check(title.Enabled&&detail.Enabled&&meter.Enabled,"progress readable while busy: "+c.Name);Check(!form.Visible,"form never shown: "+c.Name);
   }
   Set(form,"submittingVideo",false);form.ApplyStatus(Status(Sampling(10,20)));Check(detail.Text.Contains("第 2 / 3 条")&&detail.Text.Contains("01:05"),"actual native elapsed/task text");
   form.ShowBoundCard();Check(form.Page==1&&(bool)Field(form,"showingBound"),"bound page fixture");form.ApplyStatus(Status(Sampling(11,20)));Check(form.Page==1&&(bool)Field(form,"showingBound"),"progress update preserves bound page");Check(!title.Visible&&!meter.Visible&&!detail.Visible,"progress hidden on bound page");
   form.ShowPage(2);form.ApplyStatus(Status(Control(false,"batch_done")));Check(form.Page==2,"progress update preserves library page");
   form.ShowPage(0);form.LayoutScale(1);Check(title.Bottom<=meter.Top&&meter.Bottom<=detail.Top,"progress controls do not overlap");Check(detail.Bottom<=form.ClientSize.Height,"progress stays within the client area");
   Check(((System.Windows.Forms.Timer)Field(form,"poll")).Enabled==false,"no poll timer");Check(((System.Windows.Forms.Timer)Field(form,"saveDraft")).Enabled==false,"no save timer");Check(((System.Windows.Forms.Timer)Field(meter,"animation")).Enabled==false,"hidden progress animation stopped");
   // Offscreen painting exercises determinate, marquee, idle/failure and tiny sizes
   // without taking focus or creating a visible desktop window.
   var paintCases=new[]{cases.Find(c=>c.Name=="ready idle"),cases.Find(c=>c.Name=="old controller busy fallback"),cases.Find(c=>c.Name=="verified batch done"),cases.Find(c=>c.Name=="failed"),cases.Find(c=>c.Name=="sampling floor")};
   foreach(bool dark in new[]{true,false})foreach(var size in new[]{new Size(310,9),new Size(1,1)})foreach(var c in paintCases){meter.Dark=dark;meter.Size=size;meter.SetProgress(GenerationProgressState.From(c.Control,true,false));using(var bitmap=new Bitmap(Math.Max(1,size.Width),Math.Max(1,size.Height)))meter.DrawToBitmap(bitmap,new Rectangle(Point.Empty,size));Check(!form.Visible,"offscreen paint "+dark+"/"+size+"/"+c.Name);}
  }
  Check(requests==0,"zero injected API requests");Console.WriteLine("PASS: "+cases.Count+" progress cases, "+assertions+" assertions including hidden native controls and offscreen paint; API requests=0; forms shown=0.");return 0;
 }catch(Exception e){Console.WriteLine("FAIL: "+e.GetType().Name+": "+e.Message);return 1;}}
}
}
#endif
