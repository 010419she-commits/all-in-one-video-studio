// Offline native duration regression. Compile the five production sources plus
// this file with /define:DURATION_CHOICE_CHECK and
// /main:LocalVideoStudio.DurationChoiceCheck. Never calls Show or a submit action.
#if DURATION_CHOICE_CHECK
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

namespace LocalVideoStudio {
static class DurationChoiceCheck {
 static int assertions,requests;
 static readonly int[] Expected={3,4,5,6,8,10,12,15,20,30};
 static void Check(bool value,string name){assertions++;if(!value)throw new InvalidOperationException("FAILED: "+name);}
 static object Field(StudioForm form,string name){return typeof(StudioForm).GetField(name,BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form);}
 static Shot Current(StudioForm form){return (Shot)typeof(StudioForm).GetProperty("Current",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(form,null);}
 static void Load(StudioForm form){typeof(StudioForm).GetMethod("LoadShot",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(form,null);}
 static void Edit(StudioForm form){typeof(StudioForm).GetMethod("EditCurrent",BindingFlags.Instance|BindingFlags.NonPublic).Invoke(form,null);}
 static void Reject(Action action,string name){bool rejected=false;try{action();}catch(InvalidOperationException){rejected=true;}Check(rejected,name);}
 static void RoundTrip(string work,int seconds){
  var shot=Shot.Create(1);shot.Seconds=seconds;shot.Prompt="Offline duration fixture";shot.Validate();
  Check(shot.Seconds==seconds,"Validate preserves "+seconds);
  string path=Path.Combine(work,"draft-"+seconds+".json");
  DraftStore.SaveTo(path,new[]{shot});var loaded=DraftStore.LoadFrom(path);
  Check(loaded.Count==1&&loaded[0].Seconds==seconds,"SaveTo/LoadFrom preserves "+seconds);
  Check(loaded[0].Id==shot.Id,"draft identity preserved "+seconds);
 }

 [STAThread] static int Main(){
  string temporaryRoot=Path.GetFullPath(Path.GetTempPath());
  string work=Path.Combine(temporaryRoot,"FrameDurationChoice-"+Guid.NewGuid().ToString("N"));
  try{
   Directory.CreateDirectory(work);
   Check(Shot.Create(1).Seconds==5,"original new-shot default remains five");
   Check(StudioForm.VideoDurations.Length==Expected.Length,"preset duration count");
   for(int i=0;i<Expected.Length;i++)Check(StudioForm.VideoDurations[i]==Expected[i],"preset array "+Expected[i]);
   foreach(int seconds in new[]{5,10,15,20,30})RoundTrip(work,seconds);
   var invalid=Shot.Create(1);invalid.Seconds=31;
   Reject(delegate{invalid.Validate();},"31 rejected by Shot.Validate");
   string invalidPath=Path.Combine(work,"invalid-31.json");
   Reject(delegate{DraftStore.SaveTo(invalidPath,new[]{invalid});},"31 rejected by draft save");
   Check(!File.Exists(invalidPath),"invalid draft not written");

   Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
   using(var form=new StudioForm(true,delegate(string path,object body,int timeout){requests++;throw new InvalidOperationException("API forbidden in duration QA");})){
    IntPtr handle=form.Handle;
    Check(form.IsHandleCreated&&!form.Visible,"hidden handle without Show");
    var duration=(ChoiceBox)Field(form,"duration");
    Check(duration.Items.Count==Expected.Length,"all preset choices displayed");
    Check(Current(form).Seconds==5&&((Choice)duration.SelectedItem).Id=="5","form default remains five");
    for(int i=0;i<Expected.Length;i++){
     var choice=duration.Items[i] as Choice;
     Check(choice!=null&&choice.Id==Expected[i].ToString()&&choice.Label==Expected[i]+" 秒","visible choice maps "+Expected[i]);
     duration.SelectedIndex=i;
     Check(Current(form).Seconds==Expected[i],"selection edits actual Shot.Seconds "+Expected[i]);
     Current(form).Validate();Check(Current(form).Seconds==Expected[i],"selected duration validates "+Expected[i]);
    }
    // A valid older draft may contain a non-preset integer. Load must append a
    // matching Choice instead of silently selecting five seconds or using an index.
    var legacy=Current(form);legacy.Seconds=17;legacy.Validate();
    string legacyPath=Path.Combine(work,"legacy-17.json");DraftStore.SaveTo(legacyPath,new[]{legacy});
    var reopened=DraftStore.LoadFrom(legacyPath);Check(reopened[0].Seconds==17,"non-preset draft roundtrip");
    ((List<Shot>)Field(form,"shots"))[0]=reopened[0];Load(form);
    Check(Current(form).Seconds==17&&((Choice)duration.SelectedItem).Id=="17","LoadShot preserves seventeen");
    Check(duration.Items.Count==Expected.Length+1,"non-preset choice appended once");
    Load(form);Check(duration.Items.Count==Expected.Length+1,"reloading does not duplicate seventeen");
    Edit(form);Check(Current(form).Seconds==17,"EditCurrent reads seventeen ID");
    Current(form).Seconds=10;Load(form);Check(Current(form).Seconds==10&&((Choice)duration.SelectedItem).Id=="10","old ten-second shot retained");
    Current(form).Seconds=5;Load(form);Check(Current(form).Seconds==5&&((Choice)duration.SelectedItem).Id=="5","old five-second shot retained");
    Check(!((System.Windows.Forms.Timer)Field(form,"poll")).Enabled&&!((System.Windows.Forms.Timer)Field(form,"saveDraft")).Enabled,"no poll or draft timers started");
    Check(!form.Visible,"form remained hidden");
   }
   Check(requests==0,"zero API requests");
   Console.WriteLine("PASS: "+assertions+" assertions; all 10 duration choices, 5/10/15/20/30 draft roundtrips, 31 rejection, legacy 17 preserved; API=0; Show=0.");return 0;
  }catch(Exception e){Console.WriteLine("FAIL: "+e.GetType().Name+": "+e.Message);return 1;}
  finally{
   string resolved=Path.GetFullPath(work);
   string prefix=temporaryRoot.TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar)+Path.DirectorySeparatorChar;
   if(resolved.StartsWith(prefix,StringComparison.OrdinalIgnoreCase)&&Path.GetFileName(resolved).StartsWith("FrameDurationChoice-",StringComparison.Ordinal)&&Directory.Exists(resolved))Directory.Delete(resolved,true);
  }
 }
}
}
#endif
