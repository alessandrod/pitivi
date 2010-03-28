
import gst
from pitivi.elements.titlesrc import TitleSource
from pitivi.elements.imagefreeze import ImageFreeze
from pitivi.factories.base import SourceFactory
from pitivi.stream import VideoStream

id = 0

def make_id():
    global id
    id += 1
    return id - 1

class TitleSourceFactory(SourceFactory):
    def __init__(self, **kw):
        SourceFactory.__init__(self, "title://%d" % make_id(),
            kw['text'])

        caps = gst.Caps('video/x-raw-yuv; video/x-raw-rgb')
        self.addOutputStream(VideoStream(caps))
        self.source_kw = kw
        self.default_duration = 5 * gst.SECOND

    def _makeDefaultBin(self):
        return self._makeStreamBin(self.output_streams[0])

    def _makeStreamBin(self, output_stream=None):
        bin = gst.Bin()
        src = TitleSource(**self.source_kw)
        freeze = ImageFreeze()
        csp = gst.element_factory_make('ffmpegcolorspace')
        capsfilter = gst.element_factory_make('capsfilter')
        capsfilter.props.caps = output_stream.caps.copy()

        bin.add(src, freeze, csp, capsfilter)
        gst.element_link_many(src, freeze, csp, capsfilter)

        target = capsfilter.get_pad('src')
        ghost = gst.GhostPad('src', target)
        bin.add_pad(ghost)

        return bin

    def _releaseBin(self, bin):
        pass

