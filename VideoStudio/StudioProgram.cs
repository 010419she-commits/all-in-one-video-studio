using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;
namespace LocalVideoStudio {
static class Program {
 [DllImport("user32.dll")]static extern bool ShowWindow(IntPtr h,int command);
 [DllImport("user32.dll")]static extern bool SetForegroundWindow(IntPtr h);
 [STAThread]static void Main(string[] args){
  Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  Application.ThreadException+=delegate(object s,System.Threading.ThreadExceptionEventArgs e){
   LogCrash("UI",e.Exception);
   try{foreach(Form form in Application.OpenForms){var studio=form as StudioForm;if(studio!=null&&!studio.IsDisposed)studio.ReportUiError();}}catch(Exception reportError){LogCrash("UI error reporting",reportError);}
  };
  AppDomain.CurrentDomain.UnhandledException+=delegate(object s,UnhandledExceptionEventArgs e){LogCrash("Domain",e.ExceptionObject as Exception);};
  if(args.Length>0&&(args[0]=="--visual-check"||args[0]=="--visual-check-live")){VisualCheck(args[0]=="--visual-check-live");return;}
  bool created;using(var mutex=new System.Threading.Mutex(true,"Local\\FrameVideoStudio",out created)){
   if(!created){foreach(var process in Process.GetProcessesByName("VideoStudio")){try{if(process.MainWindowHandle!=IntPtr.Zero){ShowWindow(process.MainWindowHandle,9);SetForegroundWindow(process.MainWindowHandle);break;}}catch{}}return;}
   try{var form=new StudioForm();if(Array.IndexOf(args,"--connections")>=0)form.Shown+=delegate{form.ShowPage(1);};Application.Run(form);}
   catch(Exception ex){LogCrash("Startup",ex);MessageBox.Show("工作台未能正常打开，错误已记录。密钥与草稿保留，可重新打开重试。","视频工作台",MessageBoxButtons.OK,MessageBoxIcon.Error);}
  }
 }
 static void LogCrash(string kind,Exception ex){
  if(ex==null)return;
  try{
   var text=new StringBuilder().Append('[').Append(DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")).Append("] ").AppendLine(kind);
   // Type and stack are enough to diagnose GUI failures. Never persist exception messages or request bodies.
   for(Exception current=ex;current!=null;current=current.InnerException)text.AppendLine(current.GetType().FullName).AppendLine(current.StackTrace??"");
   File.AppendAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"crash.log"),text.AppendLine().ToString());
  }catch{}
 }
 static void VisualCheck(bool live){
  string root=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"qa","hybrid");Directory.CreateDirectory(root);
  using(var form=new StudioForm(true)){
   form.StartPosition=FormStartPosition.Manual;form.Location=new Point(-30000,-30000);form.ShowInTaskbar=false;form.Show();Application.DoEvents();
   var report=new StringBuilder();
   if(live){var status=Api.Request("/status");if(Json.Str(status,"service")!="video-studio")throw new InvalidOperationException("Wrong local service");form.ApplyStatus(status);report.AppendLine("READONLY LIVE STATUS: applied; no POST or GPU actions");}
   foreach(float scale in new[]{1f,1.5f,2f}){
    form.LayoutScale(scale);
    foreach(bool dark in new[]{false,true}){
     form.SetTheme(dark,false);
     for(int page=0;page<3;page++){
      if(page==1)form.EnterKeyEdit();else form.ShowPage(page);Application.DoEvents();
      Capture(form,root,(dark?"dark":"light")+"-"+((int)(scale*100))+"-"+page+".png",report);
      foreach(Control c in form.Controls)if(c.Visible&&(c.Left<0||c.Top<0||c.Right>form.ClientSize.Width||c.Bottom>form.ClientSize.Height))report.AppendLine("FAIL Bounds: "+c.Name+" "+c.Text);
     }
     if(!form.HasSavedKey){form.ApplyConnectionSettings(new System.Collections.Generic.Dictionary<string,object>{{"key_saved",true},{"kimi_model","kimi-k2.6"},{"kimi_base_url","https://api.moonshot.cn/v1"},{"agent","kimi_api"}},true);report.AppendLine("Bound layout uses public settings fixture; no key or request.");}
     form.ShowBoundCard();Application.DoEvents();
     Capture(form,root,(dark?"dark":"light")+"-"+((int)(scale*100))+"-bound.png",report);
    }
   }
   File.WriteAllText(Path.Combine(root,"visual-check.txt"),report.ToString());form.Close();
  }
 }
 static void Capture(StudioForm form,string root,string name,StringBuilder report){using(var image=new Bitmap(form.Width,form.Height)){form.DrawToBitmap(image,new Rectangle(Point.Empty,form.Size));image.Save(Path.Combine(root,name));report.AppendLine("PASS render "+name);}}
}
}
