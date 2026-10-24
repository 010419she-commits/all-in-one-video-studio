// Offline connection-flow regression. Only a hidden handle and a fake transport.
// Never calls Program.Main, Show, Api.Request, a model, or a configuration writer.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace LocalVideoStudio {
internal static class ConnectionFlowCheck {
 const BindingFlags Fields=BindingFlags.Instance|BindingFlags.NonPublic;
 const string Placeholder="OFFLINE_PLACEHOLDER_NOT_A_REAL_CREDENTIAL";
 static readonly List<Dictionary<string,object>> Results=new List<Dictionary<string,object>>();
 static int fakeRequests;

 static Dictionary<string,object> Obj(params object[] pairs){var d=new Dictionary<string,object>();for(int i=0;i<pairs.Length;i+=2)d.Add((string)pairs[i],pairs[i+1]);return d;}
 static Dictionary<string,object> Settings(bool saved,string model="fixture-model",string url="https://fixture.invalid/v1"){
  return Obj("key_saved",saved,"kimi_model",model,"kimi_base_url",url,"agent","kimi_api");
 }
 static Dictionary<string,object> Status(object settings){return Obj("service","video-studio","api_version",1,"settings",settings,"controller",Obj("busy",false,"strata_healthy",false,"comfy_running",false),"catalog",new object[0],"tasks",new object[0],"outputs",new object[0]);}
 static T Field<T>(StudioForm form,string name){return (T)typeof(StudioForm).GetField(name,Fields).GetValue(form);}
 static void SetField(StudioForm form,string name,object value){typeof(StudioForm).GetField(name,Fields).SetValue(form,value);}
 static TextBox Text(StudioForm form,string name){return Field<TextBox>(form,name);}
 static void Check(bool passed,string code){if(!passed)throw new InvalidOperationException(code);}

 sealed class FakeTransport {
  internal readonly List<string> Paths=new List<string>();
  internal readonly List<object> Bodies=new List<object>();
  internal Func<string,object,Dictionary<string,object>> Reply;
  internal Dictionary<string,object> Request(string path,object body,int timeout){
   lock(Paths){Paths.Add(path);Bodies.Add(body);Interlocked.Increment(ref fakeRequests);}
   if(path!="/settings"&&path!="/test-connection")throw new InvalidOperationException("UNEXPECTED_FAKE_ROUTE");
   if(Reply==null)throw new InvalidOperationException("UNEXPECTED_FAKE_REQUEST");
   return Reply(path,body);
  }
 }
 sealed class Fixture:IDisposable {
  internal readonly FakeTransport Transport=new FakeTransport();
  internal readonly StudioForm Form;
  internal Fixture(bool saved){
   Form=new StudioForm(true,Transport.Request);
   Form.ShowInTaskbar=false;
   IntPtr handle=Form.Handle; // CreateHandle only: do not Show or activate a window.
   Check(handle!=IntPtr.Zero&&!Form.Visible&&Form.Testing,"HIDDEN_TEST_FORM_REQUIRED");
   SetField(Form,"connected",true);
   Form.ApplyConnectionSettings(Settings(saved),true);
   Form.ShowPage(1);
  }
  public void Dispose(){Form.Close();Form.Dispose();Application.DoEvents();}
 }
 static void Pump(Task task,StudioForm form){
  var watch=Stopwatch.StartNew();
  while(!task.IsCompleted&&watch.ElapsedMilliseconds<5000){Application.DoEvents();Thread.Sleep(1);}
  Check(task.IsCompleted,"ASYNC_FIXTURE_TIMEOUT");
  task.GetAwaiter().GetResult();Application.DoEvents();
  Check(!form.Visible,"TEST_FORM_MUST_REMAIN_HIDDEN");
 }
 static void Input(StudioForm form,string model="edited-fixture-model",string url="https://edited.fixture.invalid/v1"){
  form.EnterKeyEdit();Text(form,"key").Text=Placeholder;Text(form,"apiModel").Text=model;Text(form,"baseUrl").Text=url;
 }
 static void BoundOnly(StudioForm form){
  Check(form.Page==1&&form.IsBoundView,"BOUND_PAGE_REQUIRED");
  // Control.Visible inherits the hidden Form's visibility. Read only the local
  // WinForms STATE_VISIBLE flag to verify pages without ever showing the Form.
  MethodInfo ownState=typeof(Control).GetMethod("GetState",Fields);
  Check(ownState!=null,"LOCAL_VISIBILITY_STATE_UNAVAILABLE");
  foreach(string group in new[]{"creation","connections","library","bound"})
   foreach(Control control in Field<List<Control>>(form,group)){
    bool localVisible=(bool)ownState.Invoke(control,new object[]{2});
    Check(localVisible==(group=="bound"),"MIXED_PAGE_"+group.ToUpperInvariant());
   }
 }
 static void OneSettingsCall(Fixture f){Check(f.Transport.Paths.Count==1&&f.Transport.Paths[0]=="/settings","ONLY_ONE_FAKE_SETTINGS_POST_REQUIRED");}
 static void Run(string name,Action action){
  try{action();Results.Add(Obj("case",name,"passed",true));}
  catch(Exception error){var e=error is TargetInvocationException&&error.InnerException!=null?error.InnerException:error;Results.Add(Obj("case",name,"passed",false,"exception_type",e.GetType().FullName,"assertion",e is InvalidOperationException?e.Message:"UNEXPECTED_FIXTURE_EXCEPTION"));}
 }

 [STAThread]
 internal static int Main(){
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  Run("saved_key_opens_bound_connection_page",delegate{
   using(var f=new Fixture(true)){Check(f.Form.HasSavedKey,"SAVED_KEY_STATE_REQUIRED");BoundOnly(f.Form);Check(f.Transport.Paths.Count==0,"OPENING_MUST_NOT_REQUEST");}
  });
  Run("poll_preserves_replacement_input",delegate{
   using(var f=new Fixture(true)){
    Input(f.Form);f.Form.ApplyConnectionSettings(Settings(true),false);
    Check(!f.Form.IsBoundView&&f.Form.Page==1,"EDIT_VIEW_MUST_REMAIN_OPEN");
    Check(Text(f.Form,"key").Text==Placeholder&&Text(f.Form,"apiModel").Text=="edited-fixture-model"&&Text(f.Form,"baseUrl").Text=="https://edited.fixture.invalid/v1","POLL_MUST_NOT_STEAL_EDITOR_INPUT");
   }
  });
  Run("save_uses_post_response_without_status_fetch",delegate{
   using(var f=new Fixture(false)){
    Input(f.Form);f.Transport.Reply=delegate{return Obj("ok",true,"settings",Settings(true,"posted-model","https://posted.fixture.invalid/v1"));};
    Pump(f.Form.SaveSettings(),f.Form);OneSettingsCall(f);BoundOnly(f.Form);
    Check(f.Form.HasSavedKey&&Text(f.Form,"key").Text.Length==0,"SUCCESS_CLEARS_ONLY_CONFIRMED_INPUT");
    Check(Field<Label>(f.Form,"boundModel").Text=="posted-model"&&Field<Label>(f.Form,"boundUrl").Text=="https://posted.fixture.invalid/v1","BOUND_DETAILS_MUST_USE_POST_RESPONSE");
   }
  });
  Run("failed_save_preserves_input_and_old_binding",delegate{
   using(var f=new Fixture(true)){
    Input(f.Form);f.Transport.Reply=delegate{throw new InvalidOperationException("OFFLINE_EXPECTED_SAVE_FAILURE");};
    Pump(f.Form.SaveSettings(),f.Form);OneSettingsCall(f);
    Check(f.Form.HasSavedKey&&!f.Form.IsBoundView&&Text(f.Form,"key").Text==Placeholder,"FAILED_SAVE_MUST_PRESERVE_INPUT_AND_BINDING");
   }
  });
  Run("missing_settings_does_not_confirm_save",delegate{
   using(var f=new Fixture(false)){
    Input(f.Form);f.Transport.Reply=delegate{return Obj("ok",true);};Pump(f.Form.SaveSettings(),f.Form);OneSettingsCall(f);
    Check(!f.Form.HasSavedKey&&!f.Form.IsBoundView&&Text(f.Form,"key").Text==Placeholder,"MISSING_SETTINGS_MUST_KEEP_INPUT");
   }
  });
  Run("new_key_not_saved_preserves_input_and_old_binding",delegate{
   using(var f=new Fixture(true)){
    Input(f.Form);f.Transport.Reply=delegate{return Obj("ok",true,"settings",Settings(false));};Pump(f.Form.SaveSettings(),f.Form);OneSettingsCall(f);
    Check(f.Form.HasSavedKey&&!f.Form.IsBoundView&&Text(f.Form,"key").Text==Placeholder,"UNCONFIRMED_NEW_KEY_MUST_KEEP_INPUT_AND_OLD_BINDING");
   }
  });
  Run("empty_key_save_preserves_existing_binding",delegate{
   using(var f=new Fixture(true)){
    f.Form.EnterKeyEdit();Text(f.Form,"key").Text="";
    f.Transport.Reply=delegate{return Obj("ok",true,"settings",Settings(true));};Pump(f.Form.SaveSettings(),f.Form);OneSettingsCall(f);BoundOnly(f.Form);
    var body=f.Transport.Bodies[0] as Dictionary<string,object>;
    Check(body!=null&&!body.ContainsKey("kimi_api_key")&&f.Form.HasSavedKey,"EMPTY_EDITOR_MUST_NOT_SEND_EMPTY_KEY");
   }
  });
  Run("confirmed_remove_returns_to_key_form",delegate{
   using(var f=new Fixture(true)){
    f.Transport.Reply=delegate{return Obj("ok",true,"settings",Settings(false));};Pump(f.Form.RemoveKey(),f.Form);OneSettingsCall(f);
    Check(!f.Form.HasSavedKey&&!f.Form.IsBoundView&&f.Form.Page==1&&Text(f.Form,"key").Text.Length==0,"CONFIRMED_REMOVE_MUST_RETURN_TO_FORM");
   }
  });
  Run("failed_remove_keeps_bound_page",delegate{
   using(var f=new Fixture(true)){
    f.Transport.Reply=delegate{throw new InvalidOperationException("OFFLINE_EXPECTED_REMOVE_FAILURE");};Pump(f.Form.RemoveKey(),f.Form);OneSettingsCall(f);
    Check(f.Form.HasSavedKey,"FAILED_REMOVE_MUST_KEEP_BINDING");BoundOnly(f.Form);
   }
  });
  Run("unconfirmed_remove_keeps_bound_page",delegate{
   using(var f=new Fixture(true)){
    f.Transport.Reply=delegate{return Obj("ok",true,"settings",Settings(true));};Pump(f.Form.RemoveKey(),f.Form);OneSettingsCall(f);
    Check(f.Form.HasSavedKey,"UNCONFIRMED_REMOVE_MUST_KEEP_BINDING");BoundOnly(f.Form);
   }
  });
  Run("unavailable_model_check_retains_binding_and_reports_problem",delegate{
   using(var f=new Fixture(true)){
    f.Transport.Reply=delegate{return Obj("ok",true,"model_available",false,"detail","OFFLINE_MODEL_UNAVAILABLE");};Pump(f.Form.CheckConnection(),f.Form);
    Check(f.Transport.Paths.Count==1&&f.Transport.Paths[0]=="/test-connection","ONLY_FAKE_CONNECTION_CHECK_REQUIRED");
    Check(f.Form.HasSavedKey&&Field<string>(f.Form,"lastError")=="OFFLINE_MODEL_UNAVAILABLE"&&Field<Label>(f.Form,"boundCheckState").Text=="OFFLINE_MODEL_UNAVAILABLE","UNAVAILABLE_MODEL_MUST_NOT_DELETE_BINDING");BoundOnly(f.Form);
   }
  });
  Run("stale_status_cannot_replace_new_confirmed_settings",delegate{
   using(var f=new Fixture(false)){
    Input(f.Form);f.Transport.Reply=delegate{return Obj("ok",true,"settings",Settings(true,"confirmed-model","https://confirmed.fixture.invalid/v1"));};
    Pump(f.Form.SaveSettings(),f.Form);f.Form.ApplyStatus(Status(Settings(false,"stale-model","https://stale.fixture.invalid/v1")),false);
    Check(f.Form.HasSavedKey&&Field<Label>(f.Form,"boundModel").Text=="confirmed-model"&&Text(f.Form,"apiModel").Text=="confirmed-model","STALE_STATUS_MUST_NOT_OVERRIDE_CONFIRMED_STATE");
    Check(Json.Yes(Json.Get(Field<Dictionary<string,object>>(f.Form,"latest"),"settings"),"key_saved"),"LATEST_SETTINGS_MUST_REMAIN_CONFIRMED");BoundOnly(f.Form);OneSettingsCall(f);
   }
  });
  Run("bound_card_from_other_pages_never_mixes_pages",delegate{
   using(var f=new Fixture(true))foreach(int page in new[]{0,2}){f.Form.ShowPage(page);f.Form.ShowBoundCard();BoundOnly(f.Form);}
  });
  int failed=0;foreach(var result in Results)if(!(bool)result["passed"])failed++;
  Console.WriteLine(new JavaScriptSerializer().Serialize(Obj("suite","offline_connection_flow","passed",Results.Count-failed,"failed",failed,"fake_transport_requests",fakeRequests,"hidden_handle_only",true,"real_credentials_used",false,"network_requests",0,"model_or_gpu_actions",0,"user_configuration_writes",0,"cases",Results)));
  return failed==0?0:1;
 }
}
}
