
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
        self.filter_caps = None

    def _makeDefaultBin(self):
        return self._makeStreamBin(self.output_streams[0])

    def _makeStreamBin(self, output_stream=None):
        # The project should have told us what its caps are.
        assert self.filter_caps

        bin = gst.Bin()
        bin.src = TitleSource(**self.source_kw)
        pad = bin.src.get_pad('src')
        pad.set_caps(self.filter_caps)

        freeze = ImageFreeze()
        csp = gst.element_factory_make('ffmpegcolorspace')
        capsfilter = gst.element_factory_make('capsfilter')
        capsfilter.props.caps = output_stream.caps.copy()

        bin.add(bin.src, freeze, csp, capsfilter)
        gst.element_link_many(bin.src, freeze, csp, capsfilter)

        target = capsfilter.get_pad('src')
        ghost = gst.GhostPad('src', target)
        bin.add_pad(ghost)

        return bin

    def setFilterCaps(self, caps):
        assert not caps.is_empty()
        assert caps[0].has_field('width')
        assert caps[0].has_field('height')
        # All we really care about is getting the resolution right; other
        # elements will take care of the other aspects.
        self.filter_caps = gst.Caps('video/x-raw-rgb,depth=32,bpp=32')
        self.filter_caps[0]['width'] = caps[0]['width']
        self.filter_caps[0]['height'] = caps[0]['height']

        for bin in self.bins:
            pad = bin.src.get_pad('src')
            pad.set_caps(self.filter_caps)

    def _releaseBin(self, bin):
        pass

    def set(self, **props):
        self.source_kw.update(props)
        self.name = self.source_kw['text']

        # XXX: Propagate changes to track objects here.

