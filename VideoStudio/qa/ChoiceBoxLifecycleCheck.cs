// Standalone offline UI regression. Compile StudioCommon.cs plus this file with
// /define:CHOICEBOX_LIFECYCLE_CHECK and WinForms/Drawing/Web.Extensions references.
// Never creates StudioForm, calls Api, reads settings, or switches the visible
// desktop. Fail closed if a separate non-interactive desktop cannot be created.
#if CHOICEBOX_LIFECYCLE_CHECK
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace LocalVideoStudio {
// Only the drawing helper needed to compile StudioCommon in isolation.
static class StudioForm {
 internal static GraphicsPath Rounded(RectangleF r,float radius){float d=radius*2;var p=new GraphicsPath();p.AddArc(r.X,r.Y,d,d,180,90);p.AddArc(r.Right-d,r.Y,d,d,270,90);p.AddArc(r.Right-d,r.Bottom-d,d,d,0,90);p.AddArc(r.X,r.Bottom-d,d,d,90,90);p.CloseFigure();return p;}
}

static class ChoiceBoxLifecycleCheck {
 [DllImport("user32.dll",SetLastError=true,CharSet=CharSet.Unicode)] static extern IntPtr CreateDesktop(string name,string device,IntPtr devmode,int flags,uint access,IntPtr attributes);
 [DllImport("user32.dll",SetLastError=true)] static extern bool SetThreadDesktop(IntPtr desktop);
 [DllImport("user32.dll",SetLastError=true)] static extern bool CloseDesktop(IntPtr desktop);
 static readonly MethodInfo Open=typeof(ChoiceBox).GetMethod("OpenMenu",BindingFlags.Instance|BindingFlags.NonPublic);
 static readonly FieldInfo Menu=typeof(ChoiceBox).GetField("openMenu",BindingFlags.Instance|BindingFlags.NonPublic);
 static int assertions;
 static void Check(bool value,string name){assertions++;if(!value)throw new InvalidOperationException("FAILED: "+name);}
 static void OpenChoice(ChoiceBox box){try{Open.Invoke(box,null);}catch(TargetInvocationException e){throw e.InnerException;}}
 static ContextMenuStrip Owned(ChoiceBox box){return (ContextMenuStrip)Menu.GetValue(box);}
 sealed class HiddenHost : Form {
  protected override bool ShowWithoutActivation {get{return true;}}
 }

 static void Regression(){
  Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
  var uiErrors=new List<string>();
  Application.ThreadException+=delegate(object s,ThreadExceptionEventArgs e){uiErrors.Add(e.Exception.GetType().Name);};
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  using(var light=new Font("Segoe UI",10f))
  using(var dark=new Font("Segoe UI",12f))
  using(var host=new HiddenHost{ShowInTaskbar=false,StartPosition=FormStartPosition.Manual,Location=new Point(-30000,-30000),ClientSize=new Size(400,160)}){
   var box=new ChoiceBox{Bounds=new Rectangle(10,10,220,34),Font=light};
   box.Items.AddRange(new object[]{"first","second","third","fourth"});box.SelectedIndex=0;
   host.Controls.Add(box);host.Show();Application.DoEvents();
   var owned=Owned(box);Check(owned!=null&&!owned.IsDisposed,"one owned menu constructed");
   int selectedEvents=0;box.SelectedIndexChanged+=delegate{selectedEvents++;};
   for(int i=0;i<80;i++){
    bool isDark=(i%2)==1;box.Dark=isDark;box.BackColor=isDark?Color.FromArgb(25,29,26):Color.White;box.ForeColor=isDark?Color.White:Color.Black;box.Font=isDark?dark:light;
    OpenChoice(box);Application.DoEvents();
    Check(object.ReferenceEquals(owned,Owned(box)),"same menu reused");
    Check(owned.Visible&&!owned.IsDisposed,"menu opens");
    Check(owned.Items.Count==box.Items.Count,"items rebuilt");
    Check(owned.BackColor==box.BackColor&&owned.ForeColor==box.ForeColor&&object.ReferenceEquals(owned.Font,box.Font),"theme and shared font refreshed");
    Check(((ToolStripMenuItem)owned.Items[box.SelectedIndex]).Checked,"current selection checked");
    int target=(box.SelectedIndex+1)%box.Items.Count;
    ((ToolStripMenuItem)owned.Items[target]).PerformClick();
    Check(box.SelectedIndex==target,"selection works");
    owned.Close(ToolStripDropDownCloseReason.ItemClicked);Application.DoEvents();
    Check(!owned.Visible&&!owned.IsDisposed,"close keeps menu alive");
   }
   Check(selectedEvents==80,"one selection event per change");
   box.Items.Add("added while hidden");OpenChoice(box);Application.DoEvents();Check(owned.Items.Count==5,"hidden item changes applied");owned.Close();Application.DoEvents();
   box.Enabled=false;OpenChoice(box);Check(!owned.Visible,"disabled choice stays closed");box.Enabled=true;
   OpenChoice(box);Application.DoEvents();Check(owned.Visible,"active-dispose setup");
   box.Dispose();Application.DoEvents();Check(owned.IsDisposed,"dispose closes and destroys owned menu");
   box.Dispose();OpenChoice(box);Application.DoEvents();Check(Owned(box)==null,"disposed choice cannot reopen");
   // Menu disposal must not destroy shared application fonts.
   using(var bitmap=new Bitmap(100,40))using(var graphics=Graphics.FromImage(bitmap)){
    Check(graphics.MeasureString("shared",light).Width>0&&graphics.MeasureString("shared",dark).Width>0,"shared fonts survive");
   }
   var other=new ChoiceBox{Bounds=new Rectangle(10,60,220,34),Font=light};other.Items.AddRange(new object[]{"one","two"});other.SelectedIndex=0;host.Controls.Add(other);
   OpenChoice(other);Application.DoEvents();var otherMenu=Owned(other);Check(otherMenu.Visible&&!otherMenu.IsDisposed,"another choice still opens after disposal");
   otherMenu.Close();Application.DoEvents();other.Dispose();Check(otherMenu.IsDisposed,"second menu disposed with owner");
   host.Close();Application.DoEvents();
  }
  Application.DoEvents();Check(uiErrors.Count==0,"no asynchronous UI exceptions");Application.ExitThread();
 }

 [STAThread] static int Main(){
  IntPtr desktop=CreateDesktop("FrameChoiceBoxCheck-"+Guid.NewGuid().ToString("N"),null,IntPtr.Zero,0,0x10000000,IntPtr.Zero);
  if(desktop==IntPtr.Zero){Console.WriteLine("FAIL: isolated desktop unavailable; no UI test ran on the user desktop. Win32="+Marshal.GetLastWin32Error());return 2;}
  Exception failure=null;
  var worker=new Thread(delegate(){try{if(!SetThreadDesktop(desktop))throw new Win32Exception(Marshal.GetLastWin32Error(),"SetThreadDesktop");Regression();}catch(Exception e){failure=e;}});
  worker.IsBackground=true;worker.SetApartmentState(ApartmentState.STA);worker.Start();
  if(!worker.Join(30000)){Console.WriteLine("FAIL: isolated UI regression timed out.");return 2;}
  bool closed=CloseDesktop(desktop);
  if(failure!=null){Console.WriteLine("FAIL: "+failure.GetType().Name+": "+failure.Message);return 1;}
  if(!closed){Console.WriteLine("FAIL: isolated desktop cleanup failed. Win32="+Marshal.GetLastWin32Error());return 2;}
  Console.WriteLine("PASS: "+assertions+" assertions; 80 real menu open/select/close cycles; isolated hidden desktop; no settings/API/model calls.");return 0;
 }
}
}
#endif
