# -*- coding: utf-8 -*-
"""
/***************************************************************************
 GroundRadiationMonitoring
                                 A QGIS plugin
 This plugin calculates the amount of received radiation. 
                              -------------------
        begin                : 2017-01-10
        git sha              : $Format:%H$
        copyright            : (C) 2017 by Michael Kala
        email                : michael.kala@email.cz
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
from PyQt4.QtCore import QSettings, QTranslator, qVersion, QCoreApplication, Qt, QFileInfo
from PyQt4.QtGui import QComboBox, QAction, QIcon, QToolButton, QFileDialog
from qgis.core import QgsMapLayerRegistry, QgsMapLayer, QGis, QgsPoint, QgsRaster, QgsProject,  QgsProviderRegistry, QgsDistanceArea
from qgis.utils import QgsMessageBar
from qgis.gui import QgsMapLayerComboBox,QgsMapLayerProxyModel
from osgeo import gdal, ogr
from math import ceil
from array import array
# Initialize Qt resources from file resources.py
import resources

# Import the code for the DockWidget
from ground_radiation_monitoring_dockwidget import GroundRadiationMonitoringDockWidget
import os.path

class GroundRadiationMonitoring:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor.

        :param iface: An interface instance that will be passed to this class
            which provides the hook by which you can manipulate the QGIS
            application at run time.
        :type iface: QgsInterface
        """
        # Save reference to the QGIS interface
        self.iface = iface

        # initialize plugin directory
        self.plugin_dir = os.path.dirname(__file__)

        # initialize locale
        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(
            self.plugin_dir,
            'i18n',
            'GroundRadiationMonitoring_{}.qm'.format(locale))

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)

            if qVersion() > '4.3.3':
                QCoreApplication.installTranslator(self.translator)

        # Declare instance attributes
        self.actions = []
        self.menu = self.tr(u'&Ground Radiation Monitoring')
        # TODO: We are going to let the user set this up in a future iteration
        self.toolbar = self.iface.addToolBar(u'GroundRadiationMonitoring')
        self.toolbar.setObjectName(u'GroundRadiationMonitoring')

        #print "** INITIALIZING GroundRadiationMonitoring"
        self.pluginIsActive = False
        self.dockwidget = None

        # add plugin icon into plugin toolbar
        self.toolButton = QToolButton()
        self.iface.addToolBarWidget(self.toolButton)

    # noinspection PyMethodMayBeStatic
    def tr(self, message):
        """Get the translation for a string using Qt translation API.

        We implement this ourselves since we do not inherit QObject.

        :param message: String for translation.
        :type message: str, QString

        :returns: Translated version of message.
        :rtype: QString
        """
        # noinspection PyTypeChecker,PyArgumentList,PyCallByClass
        return QCoreApplication.translate('GroundRadiationMonitoring', message)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None):
        """Add a toolbar icon to the toolbar.

        :param icon_path: Path to the icon for this action. Can be a resource
            path (e.g. ':/plugins/foo/bar.png') or a normal file system path.
        :type icon_path: str

        :param text: Text that should be shown in menu items for this action.
        :type text: str

        :param callback: Function to be called when the action is triggered.
        :type callback: function

        :param enabled_flag: A flag indicating if the action should be enabled
            by default. Defaults to True.
        :type enabled_flag: bool

        :param add_to_menu: Flag indicating whether the action should also
            be added to the menu. Defaults to True.
        :type add_to_menu: bool

        :param add_to_toolbar: Flag indicating whether the action should also
            be added to the toolbar. Defaults to True.
        :type add_to_toolbar: bool

        :param status_tip: Optional text to show in a popup when mouse pointer
            hovers over the action.
        :type status_tip: str

        :param parent: Parent widget for the new action. Defaults None.
        :type parent: QWidget

        :param whats_this: Optional text to show in the status bar when the
            mouse pointer hovers over the action.

        :returns: The action that was created. Note that the action is also
            added to self.actions list.
        :rtype: QAction
        """

        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)

        self.toolButton.setDefaultAction(action)

        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            pass

        if add_to_menu:
            self.iface.addPluginToMenu(
                self.menu,
                action)

        self.actions.append(action)

        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""

        icon_path = ':/plugins/GroundRadiationMonitoring/icon.png'
        self.add_action(
            icon_path,
            text=self.tr(u'Ground Radiation Monitoring'),
            callback=self.run,
            parent=self.iface.mainWindow())

    #--------------------------------------------------------------------------

    def onClosePlugin(self):
        """Cleanup necessary items here when plugin dockwidget is closed"""

        #print "** CLOSING GroundRadiationMonitoring"

        # disconnects
        self.dockwidget.closingPlugin.disconnect(self.onClosePlugin)

        # remove this statement if dockwidget is to remain
        # for reuse if plugin is reopened
        # Commented next statement since it causes QGIS crashe
        # when closing the docked window:
        # self.dockwidget = None

        self.pluginIsActive = False

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""

        #print "** UNLOAD GroundRadiationMonitoring"

        for action in self.actions:
            self.iface.removePluginMenu(
                self.tr(u'&Ground Radiation Monitoring'),
                action)
            self.iface.removeToolBarIcon(action)
        # remove the toolbar
        del self.toolbar

    #--------------------------------------------------------------------------

    def run(self):
        """Run method that loads and starts the plugin"""

        if not self.pluginIsActive:
            self.pluginIsActive = True

            #print "** STARTING GroundRadiationMonitoring"

            # dockwidget may not exist if:
            #    first run of plugin
            #    removed on close (see self.onClosePlugin method)
            if self.dockwidget == None:
                # Create the dockwidget (after translation) and keep reference
                self.dockwidget = GroundRadiationMonitoringDockWidget()

            # connect to provide cleanup on closing of dockwidget
            self.dockwidget.closingPlugin.connect(self.onClosePlugin)

            # show the dockwidget
            # TODO: fix to allow choice of dock location
            self.iface.addDockWidget(Qt.LeftDockWidgetArea, self.dockwidget)
            self.dockwidget.show()

        # Set filters for QgsMapLayerComboBoxes
        self.dockwidget.raster_box.setFilters( QgsMapLayerProxyModel.RasterLayer )
        self.dockwidget.track_box.setFilters( QgsMapLayerProxyModel.LineLayer )
        
        # Declare absolute paths to directories
        self.rasterAbsolutePath = ''
        self.trackAbsolutePath = ''
        self.saveAbsolutePath = ''
        
        self.dockwidget.load_raster.clicked.connect(self.loadRaster)
        self.dockwidget.load_track.clicked.connect(self.loadTrack)
        
        self.dockwidget.save_button.setEnabled(False)
        self.dockwidget.dir_button.clicked.connect(self.dirButton)
        self.dockwidget.save_button.clicked.connect(self.onExportRasterValues)

    def loadRaster(self):
        """Open 'Add raster layer dialog'."""
        fileName = QFileDialog.getOpenFileName(self.dockwidget,self.tr(u'Open raster'), self.rasterAbsolutePath, QgsProviderRegistry.instance().fileRasterFilters())
        if fileName:
            self.iface.addRasterLayer(fileName, QFileInfo(fileName).baseName())
            self.rasterAbsolutePath = QFileInfo(fileName).absolutePath()

    def loadTrack(self):
        """Open 'Add track layer dialog'."""
        fileName = QFileDialog.getOpenFileName(self.dockwidget,self.tr(u'Open track'), self.trackAbsolutePath, QgsProviderRegistry.instance().fileVectorFilters())
        if fileName:
            self.iface.addVectorLayer(fileName, QFileInfo(fileName).baseName(), "ogr")
            self.trackAbsolutePath = QFileInfo(fileName).absolutePath()

            # TODO: make this work for multiple layer loading
            if self.iface.activeLayer().geometryType() != QGis.Line:
                self.iface.messageBar().pushMessage(self.tr(u'Info'),
                                                     self.tr(u'Loaded layer {} does not have lineString type.').format(QFileInfo(fileName).baseName()),
                                                     level = QgsMessageBar.INFO, duration = 5)


    def onExportRasterValues(self):
        """Export sampled raster values to output CSV file.

        Prints error message when output file cannot be open for
        writing.

        When no raster or track vector layer given, than computation
        is not performed.
        """
        if not self.dockwidget.raster_box.currentLayer() or not self.dockwidget.track_box.currentLayer():
            self.iface.messageBar().pushMessage(self.tr(u'Error'),
                                                self.tr(u'No raster/track layer chosen.'),
                                                level=QgsMessageBar.CRITICAL, duration = 5)
            return

        # open output file for writing
        try:
            csvFile = open(self.saveFileName, 'wb')
        except IOError as e:
            self.iface.messageBar().pushMessage(self.tr(u'Error'),
                                                self.tr(u'Unable open {} for writing. Reason: {}').format(self.saveFileName, e),
                                                level=QgsMessageBar.CRITICAL, duration = 5)
            return

        # export values
        self.exportRasterValues(self.dockwidget.raster_box.currentLayer(),
                                self.dockwidget.track_box.currentLayer(),
                                csvFile)
        # close output file
        csvFile.close()
        
    def exportRasterValues(self, rasterLayer, trackLayer, csvFile):
        """Export sampled raster values to output CSV file.

        :rasterLayer: input raster layer (QgsRasterLayer)
        :trackLayer: linestring vector layer to be sampled (QgsVectorLayer)
        :csvFile: file descriptor of output CVS file
        """
        
        for featureIndex, feature in enumerate(trackLayer.getFeatures()):
            polyline = feature.geometry().asPolyline()
            for point in polyline:
                value = rasterLayer.dataProvider().identify(QgsPoint(point.x(),point.y()), QgsRaster.IdentifyFormatValue).results()
                for n in value.values():
                    csvFile.write('{val}{linesep}'.format(val=n, linesep=os.linesep))
                
        self.iface.messageBar().pushMessage(self.tr(u'Info'),
                                            self.tr(u'File {} saved.').format(self.saveFileName),
                                            level=QgsMessageBar.INFO, duration = 5)

    def getCoor(self, rasterLayer, trackLayer):
        """Get coordinates of vertices of sampled track.

        Prints error when user selected length of segment is not positive real number
        and computation is not performed.

        :rasterLayer: input raster layer (QgsRasterLayer)
        :trackLayer: linestring vector layer to be sampled (QgsVectorLayer)
        """

        try:
            distanceBetweenVertices = float(self.dockwidget.vertex_dist.text().replace(',', '.'))
        except ValueError:
            self.iface.messageBar().pushMessage(self.tr(u'Error'),
                                                self.tr(u'{} is not a number.').format(self.dockwidget.vertex_dist.text()),
                                                level=QgsMessageBar.CRITICAL, duration = 5)
            return

        if distanceBetweenVertices <= 0:
            self.iface.messageBar().pushMessage(self.tr(u'Error'),
                                                self.tr(u'{} is not a positive number.').format(distanceBetweenVertices),
                                                level=QgsMessageBar.CRITICAL, duration = 5)
            return
        
        # declare arrays of coordinates of vertices
        vertexX = array('i',[])
        vertexY = array('i',[])
        
        # get coordinates of vertices of uploaded track layer
        for featureIndex, feature in enumerate(trackLayer.getFeatures()):
            polyline = feature.geometry().asPolyline()
            pointCounter = 0
            while pointCounter < (len(polyline)-1):
                point1 = polyline[pointCounter]
                point2 = polyline[pointCounter+1]
                distance = self.distance(point1,point2)
                
                # check whether the input distance between vertices is longer then the distance between points
                if distance > distanceBetweenVertices:
                    newX, newY = self.sampleLine(point1,point2, distance, distanceBetweenVertices)
                    vertexX.extend(newX)
                    vertexY.extend(newY)
                else:
                    vertexX.append(point1[0])
                    vertexX.append(point2[0])
                    vertexY.append(point1[1])
                    vertexY.append(point2[1])
                pointCounter = pointCounter + 1

        return vertexX, vertexY        

    def distance(self, point1, point2):
        """Compute distance between points in metres.
        
        :point1: first point
        :point2: secound point
        """
        
        distance = QgsDistanceArea()
        distance.setEllipsoid('WGS84')
        distance.setEllipsoidalMode(True)
        d = distance.measureLine(QgsPoint(polyline[pointCounter]), QgsPoint(polyline[pointCounter+1]))
        return d
                
    def sampleLine(self,point1, point2, dist, distBetweenVertices):
        """Sample line between two points to segments of user selected length.
         
        Compute coordinates of new vertices.
        
        :point1: first point of line
        :point2: last point of line
        :dist: length of line in metres
        :distBetweenVertices: length of segment selected by user
        """

        # number of vertices, that should be added between 2 points
        vertexQuantity = ceil(dist / distanceBetweenVertices) - 1
        
        # if modulo of division of line length and 1 segment length is not 0,
        # point where last complete segment ends is computed
        if dist % distanceBetweenVertices != 0:
            shortestSegmentRel = (dist % distanceBetweenVertices) / dist
            lastPointX = point2[0] - vectorX * shortestSegmentRel
            lastPointY = point2[1] - vectorY * shortestSegmentRel
            vectorX = lastPointX - point1[0]
            vectorY = lastPointY - point1[1] 
        else:
            lastPointX = point2[0]
            lastPointY = point2[1]
            vectorX = point2[0] - point1[0]
            vectorY = point2[1] - point1[1]
        
        # compute addition to coordinates with size of 1 segment    
        addX = vectorX / vertexQuantity
        addY = vectorY / vertexQuantity
        
        # declare arrays for newly computed points
        newX = array('i',[point1[0]])
        newY = array('i',[point1[1]])
        
        # compute new points
        for n in range(1,vertexQuantity):
            newX.append((point1[0]+n*addX))
            newY.append((point1[1]+n*addY))
        newX.append(lastPointX)
        newY.append(lastPointY)
        
        return newX, newY
                        
    def dirButton(self):
        """Get the destination file."""
        self.saveFileName = QFileDialog.getSaveFileName(self.dockwidget, self.tr(u'Select destination file'), self.saveAbsolutePath, filter ="csv (*.csv)")
        self.dockwidget.save_file.setText(self.saveFileName)
        if self.saveFileName:
            self.saveAbsolutePath = QFileInfo(self.saveFileName).absolutePath()

         # Enable the saveButton if file is chosen
        if not self.dockwidget.save_file.text():
            self.dockwidget.save_button.setEnabled(False)
        else:
            self.dockwidget.save_button.setEnabled(True)
