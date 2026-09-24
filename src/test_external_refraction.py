import base64,io,unittest
import numpy as np
from PIL import Image
from external_refraction import crop_tiles,continuous_capture_rate,capture_interval_ms

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

if __name__=='__main__':unittest.main()
