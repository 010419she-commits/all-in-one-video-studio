// Offline readiness regression. Compile with production sources, /define:
// HANDOFF_READINESS_CHECK and /main:LocalVideoStudio.HandoffReadinessCheck.
// Creates one hidden handle, never Show()s a form or clicks a submit button.
#if HANDOFF_READINESS_CHECK
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Windows.Forms;

namespace LocalVideoStudio {
static class HandoffReadinessCheck {
 sealed class Case {
  internal string Name;internal object Controller;internal bool Expected,Service=true,Submitting;
 }
 static int assertions,requestCalls;
 static void Check(bool value,string name){assertions++;if(!value)throw new InvalidOperationException("FAILED: "+name);}
 static Dictionary<string,object> Controller(bool warm,bool? accept){
  var c=new Dictionary<string,object>{{"connected",true},{"busy",false},{"comfy_running",warm},{"can_handoff",false},{"supports_warm_handoff",true},{"warm_handoff_ready",true},{"comfy_idle",true},{"can_restore",false},{"accept_detail","Fixture: not ready"}};
  if(accept.HasValue)c["can_accept_batch"]=accept.Value;
  return c;
 }
 static Dictionary<string,object> With(Dictionary<string,object> c,string key,object value){var clone=new Dictionary<string,object>(c);clone[key]=value;return clone;}
 static Dictionary<string,object> Without(Dictionary<string,object> c,string key){var clone=new Dictionary<string,object>(c);clone.Remove(key);return clone;}
 static void Add(List<Case> cases,string name,object controller,bool expected){cases.Add(new Case{Name=name,Controller=controller,Expected=expected});}
 static List<Case> Cases(){
  var cases=new List<Case>();
  var cold=Controller(false,null);var warm=Controller(true,true);var newCold=Controller(false,true);
  Add(cases,"legacy cold compatible",cold,true);
  Add(cases,"legacy warm refused even with warm hints",Controller(true,null),false);
  Add(cases,"new cold ready",newCold,true);
  Add(cases,"warm ready can accept",warm,true);
  Add(cases,"unprepared draft can_handoff false does not block",With(warm,"can_handoff",false),true);
  Add(cases,"can_handoff absent does not block",Without(warm,"can_handoff"),true);
  Add(cases,"prepared cannot bypass explicit refusal",With(Controller(false,false),"can_handoff",true),false);
  Add(cases,"new cold explicit false authoritative",Controller(false,false),false);
  Add(cases,"new warm explicit false authoritative",Controller(true,false),false);
  Add(cases,"busy blocks",With(warm,"busy",true),false);
  Add(cases,"controller offline blocks",With(warm,"connected",false),false);
  cases.Add(new Case{Name="service offline blocks",Controller=warm,Expected=false,Service=false});
  cases.Add(new Case{Name="working submission blocks",Controller=warm,Expected=false,Submitting=true});
  Add(cases,"null controller",null,false);Add(cases,"string controller","unknown",false);Add(cases,"empty controller",new Dictionary<string,object>(),false);Add(cases,"array controller",new object[0],false);
  foreach(string key in new[]{"connected","busy","comfy_running"}){
   Add(cases,"missing "+key,Without(newCold,key),false);
   Add(cases,"string "+key,With(newCold,key,"false"),false);
   Add(cases,"null "+key,With(newCold,key,null),false);
   Add(cases,"numeric "+key,With(newCold,key,0),false);
  }
  foreach(object bad in new object[]{null,"true",1})Add(cases,"malformed explicit can_accept_batch "+(bad==null?"null":bad.GetType().Name),With(newCold,"can_accept_batch",bad),false);
  foreach(string key in new[]{"supports_warm_handoff","warm_handoff_ready","comfy_idle"}){
   Add(cases,"warm false "+key,With(warm,key,false),false);
   Add(cases,"warm missing "+key,Without(warm,key),false);
   Add(cases,"warm string "+key,With(warm,key,"true"),false);
  }
  return cases;
 }
 static Dictionary<string,object> Status(object controller){
  return new Dictionary<string,object>{{"service","video-studio"},{"api_version",1},{"controller",controller},
   {"settings",new Dictionary<string,object>{{"key_saved",false},{"kimi_base_url","https://api.moonshot.cn/v1"},{"kimi_model","kimi-k3"},{"agent","kimi_api"}}},
   {"agents",new object[0]},{"tasks",new object[0]},{"outputs",new object[0]},{"catalog",new object[0]}};
 }
 static object Field(StudioForm form,string name){return typeof(StudioForm).GetField(name,BindingFlags.NonPublic|BindingFlags.Instance).GetValue(form);}
 static void Set(StudioForm form,string name,object value){typeof(StudioForm).GetField(name,BindingFlags.NonPublic|BindingFlags.Instance).SetValue(form,value);}
 static void Controls(StudioForm form){typeof(StudioForm).GetMethod("SetControls",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(form,null);}

 [STAThread] static int Main(){try{
  var cases=Cases();
  foreach(var c in cases)Check(StudioForm.CanPrepareAndHandoff(c.Controller,c.Service,c.Submitting)==c.Expected,"helper: "+c.Name);
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  using(var form=new StudioForm(true,delegate(string path,object body,int timeout){requestCalls++;throw new InvalidOperationException("No requests allowed in readiness QA");})){
   IntPtr handle=form.Handle;Check(form.IsHandleCreated&&!form.Visible,"hidden handle only");
   var run=(Button)Field(form,"run");var note=(Label)Field(form,"gpuNote");
   var shot=(Shot)typeof(StudioForm).GetProperty("Current",BindingFlags.NonPublic|BindingFlags.Instance).GetValue(form,null);
   Check(shot.WorkflowPath.Length==0&&shot.Phase=="draft","unprepared fixture");
   foreach(var c in cases){
    Set(form,"working",false);form.ApplyStatus(Status(c.Controller));
    Set(form,"connected",c.Service);Set(form,"working",c.Submitting);Controls(form);
    Check(run.Enabled==c.Expected,"actual run.Enabled: "+c.Name);
    Check(!form.Visible,"no form shown: "+c.Name);
   }
   Set(form,"working",false);form.ApplyStatus(Status(Controller(true,true)));Check(run.Enabled&&note.Text.Contains("可直接交接"),"warm-ready explanation");
   form.ApplyStatus(Status(With(Controller(true,true),"busy",true)));Check(!run.Enabled&&note.Text.Contains("处理"),"busy explanation");
   form.ApplyStatus(Status(With(Controller(true,true),"connected",false)));Check(!run.Enabled&&note.Text.Contains("连接"),"offline explanation");
   Check(!((System.Windows.Forms.Timer)Field(form,"poll")).Enabled,"poll timer never started");
   Check(!((System.Windows.Forms.Timer)Field(form,"saveDraft")).Enabled,"draft timer never started");
  }
  Check(requestCalls==0,"zero injected API calls");
  Console.WriteLine("PASS: "+cases.Count+" readiness cases, "+assertions+" assertions including actual hidden-form run.Enabled; API requests=0; forms shown=0.");return 0;
 }catch(Exception e){Console.WriteLine("FAIL: "+e.GetType().Name+": "+e.Message);return 1;}}
}
}
#endif
