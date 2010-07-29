#!/usr/bin/python
# PiTiVi , Non-linear video editor
#
#       previewer.py
#
# Copyright (c) 2005, Edward Hervey <bilboed@bilboed.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

"""
Utility tools and classes for easy generation of previews
"""

import gobject
import gst
import cairo
import os
import gtk
import rsvg
from gettext import gettext as _
import pitivi.utils as utils
from pitivi.configure import get_pixmap_dir
from pitivi.elements.singledecodebin import SingleDecodeBin
from pitivi.elements.thumbnailsink import CairoSurfaceThumbnailSink
from pitivi.elements.arraysink import ArraySink
from pitivi.signalinterface import Signallable
import pitivi.stream as stream
from pitivi.settings import GlobalSettings
from pitivi.ui.zoominterface import Zoomable
from pitivi.log.loggable import Loggable
from pitivi.factories.file import PictureFileSourceFactory
from pitivi.factories.title import TitleSourceFactory
from pitivi.thumbnailcache import ThumbnailCache
from pitivi.ui.prefs import PreferencesDialog
from pitivi.receiver import receiver, handler

GlobalSettings.addConfigSection("thumbnailing")
GlobalSettings.addConfigOption("thumbnailSpacingHint",
    section="thumbnailing",
    key="spacing-hint",
    default=2,
    notify=True)

PreferencesDialog.addNumericPreference("thumbnailSpacingHint",
    section=_("Appearance"),
    label=_("Thumbnail Gap (pixels)"),
    lower=0,
    description=_("The gap between thumbnails"))

# this default works out to a maximum of ~ 1.78 MiB per factory, assuming:
# 4:3 aspect ratio
# 4 bytes per pixel
# 50 pixel height
GlobalSettings.addConfigOption("thumbnailCacheSize",
    section="thumbnailing",
    key="cache-size",
    default=250)

# the maximum number of thumbnails to enqueue at a given time. setting this to 
# a larger value will increase latency after large operations, such as zooming
GlobalSettings.addConfigOption("thumbnailMaxRequests",
    section="thumbnailing",
    key="max-requests",
    default = 10)

GlobalSettings.addConfigOption('showThumbnails',
    section = 'user-interface',
    key = 'show-thumbnails',
    default = True,
    notify = True)

PreferencesDialog.addTogglePreference('showThumbnails',
    section = _("Appearance"),
    label = _("Show Thumbnails (Video)"),
    description = _("Show Thumbnails on Video Clips"))

GlobalSettings.addConfigOption('showWaveforms',
    section = 'user-interface',
    key = 'show-waveforms',
    default = True,
    notify = True)

PreferencesDialog.addTogglePreference('showWaveforms',
    section = _("Appearance"),
    label = _("Show Waveforms (Audio)"),
    description = _("Show Waveforms on Audio Clips"))

# Previewer                      -- abstract base class with public interface for UI
# |_DefaultPreviewer             -- draws a default thumbnail for UI
# |_LivePreviewer                -- draws a continuously updated preview
# | |_LiveAudioPreviwer          -- a continously updating level meter
# | |_LiveVideoPreviewer         -- a continously updating video monitor
# |_RandomAccessPreviewer        -- asynchronous fetching and caching
#   |_RandomAccessAudioPreviewer -- audio-specific pipeline and rendering code
#   |_RandomAccessVideoPreviewer -- video-specific pipeline and rendering
#     |_StillImagePreviewer      -- only uses one segment

previewers = {}

def get_preview_for_object(instance, trackobject):
    factory = trackobject.factory
    for stream_ in factory.getOutputStreams():
        if stream_.isCompatible(trackobject.track.stream):
            break
        stream_ = None
    if not stream_:
        raise NotImplementedError
    stream_type = type(stream_)
    key = factory, stream_
    if not key in previewers:
        # TODO: handle non-random access factories
        # TODO: handle non-source factories
        # note that we switch on the stream_type, but we hash on the stream
        # itself.
        if stream_type == stream.AudioStream:
            previewers[key] = RandomAccessAudioPreviewer(instance, factory, stream_)
        elif stream_type == stream.VideoStream:
            if type(factory) in (TitleSourceFactory, PictureFileSourceFactory):
                previewers[key] = StillImagePreviewer(instance, factory, stream_)
            else:
                previewers[key] = RandomAccessVideoPreviewer(instance, factory, stream_)
        else:
            previewers[key] = DefaultPreviewer(instance, factory, stream_)
    return previewers[key]

class Previewer(Signallable, Loggable):

    __signals__ = {
        "update" : ("segment",),
    }

    # TODO: parameterize height, instead of assuming self.theight pixels.
    # NOTE: dymamically changing thumbnail height would involve flushing the
    # thumbnail cache.

    __DEFAULT_THUMB__ = "processing-clip.png"

    aspect = 4.0 / 3.0

    def __init__(self, instance, factory, stream_):
        Loggable.__init__(self)
        # create default thumbnail
        icon_theme = gtk.icon_theme_get_default()
        icon = icon_theme.lookup_icon("appointment-soon", 48, ())
        # If we can't find the appointment-soon icon, use our own
        if icon is None:
            path = os.path.join(get_pixmap_dir(), self.__DEFAULT_THUMB__)
        else:
            path = icon.get_filename()

        # If it is an SVG, we have to render to cairo using librsvg
        if path.endswith(".svg"):
            handle = rsvg.Handle(file=path)
            (width, height, width_fl, height_fl) = handle.get_dimension_data()
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
            cr = cairo.Context(surface)
            handle.render_cairo(cr)
            self.default_thumb = surface
        else:
            # Easy for PNGs
            self.default_thumb = cairo.ImageSurface.create_from_png(path)
        self._connectSettings(instance.settings)

    def render_cairo(self, cr, bounds, element, y1):
        """Render a preview of element onto a cairo context within the current
        bounds, which may or may not be the entire object and which may or may
        not intersect the visible portion of the object"""
        raise NotImplementedError

    def _connectSettings(self, settings):
        self._settings = settings

class DefaultPreviewer(Previewer):

    def render_cairo(self, cr, bounds, element, y1):
        # TODO: draw a single thumbnail
        pass

class RandomAccessPreviewer(Previewer):

    """ Handles loading, caching, and drawing preview data for segments of
    random-access streams.  There is one Previewer per stream per
    ObjectFactory.  Preview data is read from an instance of an
    ObjectFactory's Object, and when requested, drawn into a given cairo
    context. If the requested data is not cached, an appropriate filler will
    be substituted, and an asyncrhonous request for the data will be issued.
    When the data becomes available, the update signal is emitted, along with
    the stream, and time segments. This allows the UI to re-draw the affected
    portion of a thumbnail sequence or audio waveform."""

    def __init__(self, instance, factory, stream_):
        self._view = True
        Previewer.__init__(self, instance, factory, stream_)
        self._queue = []

        bin = factory.makeBin(stream_)

        # assume 50 pixel height
        self.theight = 50
        self.waiting_timestamp = None

        self.pipeline = self._pipelineInit(factory, bin)
        self.pipeline.set_state(gst.STATE_PAUSED)

        instance.connect('project-closed', self._pipelineShutdown)
        self.project = instance.current

    def _pipelineInit(self, factory, bin):
        """Create the pipeline for the preview process. Subclasses should
        override this method and create a pipeline, connecting to callbacks to
        the appropriate signals, and prerolling the pipeline if necessary."""
        raise NotImplementedError

    def _pipelineShutdown(self, instance, project):
        # Ensure that there aren't any zombie pipelines around; they can cause
        # deadlocks on exit.

        if project == self.project:
            self.pipeline.set_state(gst.STATE_NULL)

## public interface

    def render_cairo(self, cr, bounds, element, y1):
        if not self._view:
            return
        # The idea is to conceptually divide the clip into a sequence of
        # rectangles beginning at the start of the file, and
        # pixelsToNs(twidth) nanoseconds long. The thumbnail within the
        # rectangle is the frame produced from the timestamp corresponding to
        # rectangle's left edge. We speed things up by only drawing the
        # rectangles which intersect the given bounds.  FIXME: how would we
        # handle timestretch?
        height = bounds.y2 - bounds.y1
        width = bounds.x2 - bounds.x1

        # we actually draw the rectangles just to the left of the clip's in
        # point and just to the right of the clip's out-point, so we need to
        # mask off the actual bounds.
        cr.rectangle(bounds.x1, bounds.y1, width, height)
        cr.clip()

        # tdur = duration in ns of thumbnail
        # sof  = start of file in pixel coordinates
        x1 = bounds.x1;
        sof = Zoomable.nsToPixel(element.start - element.in_point)

        # i = left edge of thumbnail to be drawn. We start with x1 and
        # subtract the distance to the nearest leftward rectangle.
        # Justification of the following:
        #                i = sof + k * twidth
        #                i = x1 - delta
        # sof + k * twidth = x1 - delta
        #           i * tw = (x1 - sof) - delta
        #    <=>     delta = x1 - sof (mod twidth).
        # Fortunately for us, % works on floats in python.

        i = x1 - ((x1 - sof) % (self.twidth + self._spacing()))

        # j = timestamp *within the element* of thumbnail to be drawn. we want
        # timestamps to be numerically stable, but in practice this seems to
        # give good enough results. It might be possible to improve this
        # further, which would result in fewer thumbnails needing to be
        # generated.
        j = Zoomable.pixelToNs(i - sof)
        istep = self.twidth + self._spacing()
        jstep = self.tdur + Zoomable.pixelToNs(self.spacing)

        while i < bounds.x2:
            self._thumbForTime(cr, j, i, y1)
            cr.rectangle(i - 1, y1, self.twidth + 2, self.theight)
            i += istep
            j += jstep
            cr.fill()

    def _spacing(self):
        return self.spacing

    def _segmentForTime(self, time):
        """Return the segment for the specified time stamp. For some stream
        types, the segment duration will depend on the current zoom ratio,
        while others may only care about the timestamp. The value returned
        here will be used as the key which identifies the thumbnail in the
        thumbnail cache"""

        raise NotImplementedError

    def _thumbForTime(self, cr, time, x, y):
        segment = self._segment_for_time(time)
        if segment in self._cache:
            surface = self._cache[segment]
        else:
            self._requestThumbnail(segment)
            surface = self.default_thumb
        cr.set_source_surface(surface, x, y)

    def _finishThumbnail(self, surface, segment):
        """Notifies the preview object that the a new thumbnail is ready to be
        cached. This should be called by subclasses when they have finished
        processing the thumbnail for the current segment. This function should
        always be called from the main thread of the application."""
        waiting = self.waiting_timestamp
        self.waiting_timestamp = None

        if segment != waiting:
            segment = waiting

        self._cache[segment] = surface
        self.emit("update", segment)

        if segment in self._queue:
            self._queue.remove(segment)
        self._nextThumbnail()
        return False

    def _nextThumbnail(self):
        """Notifies the preview object that the pipeline is ready to process
        the next thumbnail in the queue. This should always be called from the
        main application thread."""
        if self._queue:
            self._startThumbnail(self._queue[0])
        return False

    def _requestThumbnail(self, segment):
        """Queue a thumbnail request for the given segment"""

        if (segment not in self._queue) and (len(self._queue) <=
            self.max_requests):
            if self._queue:
                self._queue.append(segment)
            else:
                self._queue.append(segment)
                self._nextThumbnail()

    def _startThumbnail(self, segment):
        """Start processing segment. Subclasses should override
        this method to perform whatever action on the pipeline is necessary.
        Typically this will be a flushing seek(). When the
        current segment has finished processing, subclasses should call
        _nextThumbnail() with the resulting cairo surface. Since seeking and
        playback are asyncrhonous, you may have to call _nextThumbnail() in a
        message handler or other callback."""
        self.waiting_timestamp = segment

    def _connectSettings(self, settings):
        Previewer._connectSettings(self, settings)
        self.spacing = settings.thumbnailSpacingHint
        self._cache = ThumbnailCache(size=settings.thumbnailCacheSize)
        self.max_requests = settings.thumbnailMaxRequests
        settings.connect("thumbnailSpacingHintChanged",
            self._thumbnailSpacingHintChanged)

    def _thumbnailSpacingHintChanged(self, settings):
        self.spacing = settings.thumbnailSpacingHint
        self.emit("update", None)

class RandomAccessVideoPreviewer(RandomAccessPreviewer):

    @property
    def twidth(self):
        return int(self.aspect * self.theight)

    @property
    def tdur(self):
        return Zoomable.pixelToNs(self.twidth)

    def __init__(self, instance, factory, stream_):
        if stream_.dar and stream_.par:
            self.aspect = float(stream_.dar)
        rate = stream_.framerate
        RandomAccessPreviewer.__init__(self, instance, factory, stream_)
        self.tstep = Zoomable.pixelToNsAt(self.twidth, Zoomable.max_zoom)
        if rate.num:
            frame_duration = (gst.SECOND * rate.denom) / rate.num
            self.tstep = max(frame_duration, self.tstep)

    def _pipelineInit(self, factory, sbin):
        csp = gst.element_factory_make("ffmpegcolorspace")
        sink = CairoSurfaceThumbnailSink()
        scale = gst.element_factory_make("videoscale")
        scale.props.method = 0
        caps = ("video/x-raw-rgb,height=(int) %d,width=(int) %d" %
            (self.theight, self.twidth + 2))
        filter_ = utils.filter_(caps)
        pipeline = utils.pipeline({
            sbin : csp,
            csp : scale,
            scale : filter_,
            filter_ : sink,
            sink : None
        })
        sink.connect('thumbnail', self._thumbnailCb)
        return pipeline

    def _segment_for_time(self, time):
        # quantize thumbnail timestamps to maximum granularity
        return time - (time % self.tstep)

    def _thumbnailCb(self, unused_thsink, pixbuf, timestamp):
        gobject.idle_add(self._finishThumbnail, pixbuf, timestamp)

    def _startThumbnail(self, timestamp):
        RandomAccessPreviewer._startThumbnail(self, timestamp)
        self.log("timestamp : %s", gst.TIME_ARGS(timestamp))
        self.pipeline.seek(1.0,
            gst.FORMAT_TIME, gst.SEEK_FLAG_FLUSH | gst.SEEK_FLAG_ACCURATE,
            gst.SEEK_TYPE_SET, timestamp,
            gst.SEEK_TYPE_NONE, -1)

    def _connectSettings(self, settings):
        RandomAccessPreviewer._connectSettings(self, settings)
        settings.connect("showThumbnailsChanged", self._showThumbsChanged)
        self._view = settings.showThumbnails

    def _showThumbsChanged(self, settings):
        self._view = settings.showThumbnails
        self.emit("update", None)

class StillImagePreviewer(RandomAccessVideoPreviewer):

    def _thumbForTime(self, cr, time, x, y):
        return RandomAccessVideoPreviewer._thumbForTime(self, cr, 0L, x, y)

class RandomAccessAudioPreviewer(RandomAccessPreviewer):

    def __init__(self, instance, factory, stream_):
        self.tdur = 30 * gst.SECOND
        self.base_width = int(Zoomable.max_zoom)
        RandomAccessPreviewer.__init__(self, instance, factory, stream_)

    @property
    def twidth(self):
        return Zoomable.nsToPixel(self.tdur)

    def _pipelineInit(self, factory, sbin):
        self.spacing = 0

        self.audioSink = ArraySink()
        conv = gst.element_factory_make("audioconvert")
        pipeline = utils.pipeline({
            sbin : conv,
            conv : self.audioSink,
            self.audioSink : None})
        bus = pipeline.get_bus()
        bus.set_sync_handler(self._bus_message)
        self._audio_cur = None
        return pipeline

    def _spacing(self):
        return 0

    def _segment_for_time(self, time):
        # for audio files, we need to know the duration the segment spans
        return time - (time % self.tdur), self.tdur

    def _bus_message(self, bus, message):
        if message.type == gst.MESSAGE_SEGMENT_DONE:
            self._finishWaveform()

        elif message.type == gst.MESSAGE_ERROR:
            error, debug = message.parse_error()
            # FIXME: do something intelligent here
            print "Event bus error:", str(error), str(debug)

        return gst.BUS_PASS

    def _startThumbnail(self, (timestamp, duration)):
        RandomAccessPreviewer._startThumbnail(self, (timestamp, duration))
        self._audio_cur = timestamp, duration
        self.pipeline.seek(1.0,
            gst.FORMAT_TIME,
            gst.SEEK_FLAG_FLUSH | gst.SEEK_FLAG_ACCURATE | gst.SEEK_FLAG_SEGMENT,
            gst.SEEK_TYPE_SET, timestamp,
            gst.SEEK_TYPE_SET, timestamp + duration)
        self.pipeline.set_state(gst.STATE_PLAYING)

    def _finishWaveform(self):
        surfaces = []
        surface = cairo.ImageSurface(cairo.FORMAT_A8,
            self.base_width, self.theight)
        cr = cairo.Context(surface)
        self._plotWaveform(cr, self.base_width)
        self.audioSink.reset()

        for width in [25, 100, 200]:
            scaled = cairo.ImageSurface(cairo.FORMAT_A8,
               width, self.theight)
            cr = cairo.Context(scaled)
            matrix = cairo.Matrix()
            matrix.scale(self.base_width/width, 1.0)
            cr.set_source_surface(surface)
            cr.get_source().set_matrix(matrix)
            cr.rectangle(0, 0, width, self.theight)
            cr.fill()
            surfaces.append(scaled)
        surfaces.append(surface)
        gobject.idle_add(self._finishThumbnail, surfaces, self._audio_cur)

    def _plotWaveform(self, cr, base_width):
        # clear background
        cr.set_source_rgba(1, 1, 1, 0.0)
        cr.rectangle(0, 0, base_width, self.theight)
        cr.fill()

        samples = self.audioSink.samples

        if not samples:
            return

        # find the samples-per-pixel ratio
        spp = len(samples) / base_width
        if spp == 0:
            spp = 1
        channels = self.audioSink.channels
        stride = spp * channels
        hscale = self.theight / (2 * channels)

        # plot points from min to max over a given hunk
        chan = 0
        y = hscale
        while chan < channels:
            i = chan
            x = 0
            while i < len(samples):
                slice = samples[i:i + stride:channels]
                min_ = min(slice)
                max_ = max(slice)
                cr.move_to(x, y - (min_ * hscale))
                cr.line_to(x, y - (max_ * hscale))
                i += spp
                x += 1
            y += 2 * hscale
            chan += 1

        # Draw!
        cr.set_source_rgba(0, 0, 0, 1.0)
        cr.stroke()

    def _thumbForTime(self, cr, time, x, y):
        segment = self._segment_for_time(time)
        twidth = self.twidth
        if segment in self._cache:
            surfaces = self._cache[segment]
            if twidth > 200:
                surface = surfaces[3]
                base_width = self.base_width
            elif twidth <= 200:
                surface = surfaces[2]
                base_width = 200
            elif twidth <= 100:
                surface = surfaces[1]
                base_width = 100
            elif twidth <= 25:
                surface = surfaces[0]
                base_width = 25
            x_scale = float(base_width) / self.twidth
            cr.set_source_surface(surface)
            matrix = cairo.Matrix()
            matrix.scale(x_scale, 1.0)
            matrix.translate(-x, -y)
            cr.get_source().set_matrix(matrix)
        else:
            self._requestThumbnail(segment)
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)

    def _connectSettings(self, settings):
        RandomAccessPreviewer._connectSettings(self, settings)
        self._view = settings.showWaveforms
        settings.connect("showWaveformsChanged", self._showWaveformsChanged)

    def _showWaveformsChanged(self, settings):
        self._view = settings.showWaveforms
        self.emit("update", None)

