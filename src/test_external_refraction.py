import base64,io,sys,time,types,unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import external_refraction
from external_refraction import crop_tiles,continuous_capture_rate,capture_interval_ms,capture_session_key

class EdgeCropTests(unittest.TestCase):
    def test_retains_only_bounded_border_tiles(self):
        frame=np.zeros((900,1600,4),dtype=np.uint8);frame[:]=[220,220,220,255]
        frame[350:550,500:1000,:3]=[0,0,255] # Sensitive center must never reach returned tiles.
        tiles=crop_tiles(frame,(100,100,1300,700),1.25)
        self.assertEqual({t['side'] for t in tiles},{'top','left','right','bottom'})
        count=0
        for tile in tiles:
            image=np.array(Image.open(io.BytesIO(base64.b64decode(tile['url'].split(',')[1]))))
            self.assertFalse(np.any(image[:,:,0]>image[:,:,1]+10))
            count+=image.shape[0]*image.shape[1]
            self.assertTrue(min(tile['texture_size'])<=64)
        self.assertLess(count,1300*700*.3)
    def test_off_screen_edges_do_not_wrap_to_other_side(self):
        frame=np.zeros((400,400,4),dtype=np.uint8);frame[:,:,2]=255
        tiles=crop_tiles(frame,(-15,-10,300,300))
        image=np.array(Image.open(io.BytesIO(base64.b64decode(tiles[0]['url'].split(',')[1]))))
        self.assertGreater(int(image[2,2,1]),230) # padded white, not wrapped red
    def test_rejects_implausible_geometry(self):
        self.assertEqual(crop_tiles(np.zeros((2,2,4),dtype=np.uint8),(0,0,10,10)),[])

    def test_capture_rate_uses_only_a_live_motion_burst(self):
        self.assertEqual(continuous_capture_rate([i/60 for i in range(30)],29/60),(60.,30))
        self.assertEqual(continuous_capture_rate([i/30 for i in range(12)],11/30),(30.,12))
        self.assertEqual(continuous_capture_rate([i/30 for i in range(12)],2),(None,0))

    def test_capture_request_tracks_monitor_mode_with_60_to_120_hz_bounds(self):
        self.assertEqual(capture_interval_ms(60),(16,60))
        self.assertEqual(capture_interval_ms(120),(8,120))
        self.assertEqual(capture_interval_ms(165),(8,120))
        self.assertEqual(capture_interval_ms(None),(16,60))

    def test_window_motion_keeps_one_capture_session_until_monitor_changes(self):
        class FakeGeometry:
            instance=None
            def __init__(self,*_):
                self.position=(1,0,0,800,600,10,10,300,300,1.,60)
                FakeGeometry.instance=self
            def exclude(self):pass
            def restore(self):pass
            def bounds(self):return self.position

        class FakeControl:
            def stop(self):pass
            def is_finished(self):return False

        class FakeCapture:
            instances=[]
            def __init__(self,**kwargs):
                self.kwargs=kwargs;self.events=[];FakeCapture.instances.append(self)
            def event(self,callback):self.events.append(callback)
            def start_free_threaded(self):self.control=FakeControl();return self.control

        def until(condition):
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                if condition():return
                time.sleep(.01)
            self.fail('Timed out waiting for capture geometry update')

        fake_module=types.SimpleNamespace(WindowsCapture=FakeCapture)
        with patch.object(external_refraction,'Geometry',FakeGeometry),patch.dict(sys.modules,{'windows_capture':fake_module}):
            capture=external_refraction.Refraction(1,2)
            capture.start()
            try:
                until(lambda:capture.capture_sessions_started==1)
                frame_data=np.zeros((600,800,4),dtype=np.uint8)
                frame_data[:,:,2]=np.arange(800,dtype=np.uint8)
                frame=types.SimpleNamespace(width=800,height=600,frame_buffer=frame_data)
                first=FakeCapture.instances[0]
                first.events[0](frame,first.control)
                before=Image.open(io.BytesIO(base64.b64decode(capture.tiles[0]['url'].split(',')[1])))
                original=FakeGeometry.instance.position
                FakeGeometry.instance.position=original[:5]+(80,50,300,300,1.,60)
                until(lambda:capture.capture_bounds==FakeGeometry.instance.position)
                self.assertEqual(capture.capture_sessions_started,1)
                first.events[0](frame,first.control)
                after=Image.open(io.BytesIO(base64.b64decode(capture.tiles[0]['url'].split(',')[1])))
                self.assertGreater(after.getpixel((150,10))[0]-before.getpixel((150,10))[0],60)
                FakeGeometry.instance.position=original[:5]+(80,50,400,350,1.,60)
                until(lambda:capture.capture_bounds==FakeGeometry.instance.position)
                self.assertEqual(capture.capture_sessions_started,1)
                capture.last_request=time.monotonic()-4
                time.sleep(.08)
                self.assertTrue(capture.enabled) # A stalled view does not switch off user-enabled refraction.
                FakeGeometry.instance.position=(2,800,0,800,600,100,50,400,350,1.,60)
                until(lambda:capture.capture_sessions_started==2)
                self.assertEqual(FakeCapture.instances[0].kwargs['monitor_index'],1)
                self.assertEqual(FakeCapture.instances[1].kwargs['monitor_index'],2)
                self.assertGreaterEqual(capture.geometry_updates,3)
            finally:capture.stop()

    def test_capture_session_key_ignores_position_and_size(self):
        self.assertEqual(capture_session_key((1,0,0,800,600,1,2,300,300,1.,60)),
                         capture_session_key((1,0,0,800,600,90,40,400,320,1.,60)))

if __name__=='__main__':unittest.main()
