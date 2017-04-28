# -*- coding: utf-8 -*-
"""
/***************************************************************************
 GroundRadiationMonitoringDockWidget
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

import os

from PyQt4.QtCore import QSettings, QTranslator, qVersion, QCoreApplication, Qt, QFileInfo, QThread, pyqtSignal
from PyQt4.QtGui import QComboBox, QAction, QIcon, QToolButton, QFileDialog, QMessageBox, QProgressBar, QCheckBox
from qgis.core import QgsMapLayerRegistry, QgsMapLayer, QGis, QgsPoint, QgsRaster, QgsProject,  QgsProviderRegistry, QgsDistanceArea
from qgis.utils import QgsMessageBar, iface
from qgis.gui import QgsMapLayerComboBox,QgsMapLayerProxyModel
from osgeo import gdal, ogr
from math import ceil
from array import array

import time

from PyQt4 import QtGui, uic

from ground_radiation_monitoring_computation import GroundRadiationMonitoringComputation

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'ground_radiation_monitoring_dockwidget_base.ui'))


class GroundRadiationMonitoringDockWidget(QtGui.QDockWidget, FORM_CLASS):

    closingPlugin = pyqtSignal()

    def __init__(self, parent=None):
        """Constructor."""
        super(GroundRadiationMonitoringDockWidget, self).__init__(parent)
        # Set up the user interface from Designer.
        # After setupUI you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.setupUi(self)

        self.settings = QSettings("CTU","GRMplugin")

        self.iface = iface

        # Set filters for QgsMapLayerComboBoxes
        self.raster_box.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.track_box.setFilters(QgsMapLayerProxyModel.LineLayer)

        self.load_raster.clicked.connect(self.onLoadRaster)
        self.load_track.clicked.connect(self.onLoadTrack)

        self.save_button.setEnabled(False)
        self.report_button.clicked.connect(self.onReportButton)
        #self.dir_button.clicked.connect(self.onCsvButton)
        self.shp_button.clicked.connect(self.onShpButton)
        self.save_button.clicked.connect(self.onExportRasterValues)

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()

    def onLoadRaster(self):
        """Open 'Add raster layer dialog'."""
        sender = u'{}-lastUserFilePath'.format(self.sender().objectName())
        lastUsedFilePath = self.settings.value(sender, '')

        fileName = QFileDialog.getOpenFileName(self,self.tr(u'Open raster'), 
                                               self.tr(u'{}').format(lastUsedFilePath),
                                               QgsProviderRegistry.instance().fileRasterFilters())
        if fileName:
            self.iface.addRasterLayer(fileName, QFileInfo(fileName).baseName())
            self.settings.setValue(sender, os.path.dirname(fileName))
            
    def onLoadTrack(self):
        """Open 'Add track layer dialog'."""
        sender = u'{}-lastUserFilePath'.format(self.sender().objectName())
        lastUsedFilePath = self.settings.value(sender, '')
        
        fileName = QFileDialog.getOpenFileName(self,self.tr(u'Open track'),
                                               self.tr(u'{}').format(lastUsedFilePath), 
                                               QgsProviderRegistry.instance().fileVectorFilters())
        if fileName:
            self.iface.addVectorLayer(fileName, QFileInfo(fileName).baseName(), "ogr")
            self.settings.setValue(sender, os.path.dirname(fileName))
            
            name, fileExtension = os.path.splitext(fileName)
            if fileExtension not in QgsProviderRegistry.instance().fileVectorFilters():
                return
            
            # TODO: make this work for multiple layer loading
            if self.iface.activeLayer().geometryType() != QGis.Line:
                self.sendMessage(u'Info', u'{} does not have lineString type.'.format(QFileInfo(fileName).baseName()), 'INFO')

    def onReportButton(self):
        """Get destination of report, csv and shape file.

        Set path and name for csv and shape file by default as file path for report file.
        
        Set default name for report file same as track layer name"""

        sender = u'{}-lastUserFilePath'.format(self.sender().objectName())
        lastUsedFilePath = self.settings.value(sender, '')

        self.saveReportName = QFileDialog.getSaveFileName(self, self.tr(u'Select destination file'), 
                                                          self.tr(u'{}{}.txt').format(lastUsedFilePath,os.path.sep), 
                                                          filter ="TXT (*.txt)")
        self.saveShpName = self.tr(u'{}.shp').format(self.saveReportName.split('.')[0])

        self.report_file.setText(self.tr(u'{}').format(self.saveReportName))

        if self.saveReportName:
            self.shp_file.setText(self.tr(u'{}').format(self.saveShpName))
            self.settings.setValue(sender, os.path.dirname(self.saveReportName))

         # Enable the saveButton if file is chosen
        if not self.report_file.text():
            self.save_button.setEnabled(False)
        else:
            self.save_button.setEnabled(True)

    def onShpButton(self):
        """Get destination of shp file."""

        sender = u'{}-lastUserFilePath'.format(self.sender().objectName())
        lastUsedFilePath = self.settings.value(sender, '')
        self.saveShpName = QFileDialog.getSaveFileName(self, self.tr(u'Select destination file'), 
                                                       self.tr(u'{}{}.shp').format(lastUsedFilePath,os.path.sep), 
                                                       filter ="ESRI Shapefile (*.shp)")

        self.shp_file.setText(u'{}'.format(self.saveShpName))
        
        if self.saveShpName:
            self.settings.setValue(sender, os.path.dirname(self.saveShpName))
            
        if not (self.report_file.text() and self.shp_file.text()):
            self.save_button.setEnabled(False)
        else:
            self.save_button.setEnabled(True)    

    def onExportRasterValues(self):
        """Export sampled raster values to output CSV file.

        Prints error when user selected length of segment or speed is not positive real number
        and computation is not performed.

        When no raster or track vector layer given, than computation
        is not performed.
        
        If shapefile that will be created has the same name as one of the layers in
        map canvas, that layer will be removed from map layer registry.
        """
        noSamplingFlag = False
        if not self.vertex_dist.text().strip():
            distanceBetweenVertices = 0
            noSamplingFlag = True
        
        if noSamplingFlag == False:
            try:
                distanceBetweenVertices = float(self.vertex_dist.text().replace(',', '.'))
            except ValueError:
                self.sendMessage(u'Error', u'"{}" is not a number. (distance between vertices)'.format(self.vertex_dist.text()), 'CRITICAL')
                return

        if distanceBetweenVertices <= 0 and noSamplingFlag == False:
            self.sendMessage(u'Error', u'"{}" is not a positive number. (distance between vertices)'.format(distanceBetweenVertices), 'CRITICAL')
            return

        if not self.speed.text():
            self.sendMessage(u'Error', u'No speed given.', 'CRITICAL')
            return   
        try:
            speed = float(self.speed.text().replace(',', '.'))
        except ValueError:
            self.sendMessage(u'Error', u'"{}" is not a number. (speed)'.format(self.speed.text()), 'CRITICAL')
            return

        if speed <= 0:
            self.sendMessage(u'Error', u'"{}" is not a positive number. (speed)'.format(speed), 'CRITICAL')
            return

        if not self.raster_box.currentLayer() or not self.track_box.currentLayer():
            self.sendMessage(u'Error', u'No raster/track layer chosen.', 'CRITICAL')
            return
        
        # remove layers with same name as newly created layer
        for lyr in QgsMapLayerRegistry.instance().mapLayers().values():
            if lyr.source() == self.saveShpName:
                QgsMapLayerRegistry.instance().removeMapLayer(lyr.id())
        
        self.saveCsvName = None
        
        if self.create_csv.isChecked():
            self.saveCsvName = self.tr(u'{}.csv').format(self.saveShpName.split('.')[0])            
        
        self.computeThread = GroundRadiationMonitoringComputation(self.raster_box.currentLayer().id(),
                                                                  self.track_box.currentLayer().id(),
                                                                  self.saveReportName,
                                                                  self.saveCsvName,
                                                                  self.saveShpName,
                                                                  distanceBetweenVertices,
                                                                  speed,
                                                                  self.unit_box.currentText())
        self.saveShpNameOriginal = self.saveShpName
        self.saveReportNameOriginal = self.saveReportName
        self.saveCsvNameOriginal = self.saveCsvName
        self.computeThread.computeEnd.connect(self.addNewLayer)
        self.computeThread.computeStat.connect(self.setStatus)
        self.computeThread.computeProgress.connect(self.progressBar)
        self.computeThread.computeMessage.connect(self.sendMessage)
        if not self.computeThread.isRunning():
            self.computeThread.start()

        self.save_button.setEnabled(False)


    def onCancelButton(self):
        """Show message box with question on canceling. Cancel computation."""
        
        reply  = QMessageBox.question(self, u'Ground Radiation Monitoring',
                                            u"Cancel computation?{ls}(All created files will be lost.)".format(ls = 2*os.linesep),
                                            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
                                            QtGui.QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            GroundRadiationMonitoringComputation.abortThread(self.computeThread)
           
            # kill progress bar if it is still on (if computation is still on)
            try:
                self.progress.setParent(None)
                self.iface.messageBar().popWidget(self.progressMessageBar)
            except:
                pass    
            
            self.cleanCreatedFiles()
            
            if not (self.report_file.text() and self.shp_file.text()):
                self.save_button.setEnabled(False)
            else:
                self.save_button.setEnabled(True)  

            
    def cleanCreatedFiles(self):
        """Remove created files."""
        
        # remove layers with same name as newly created layer (if is created
        # and added to map canvas) so shape file could be removed
        for lyr in QgsMapLayerRegistry.instance().mapLayers().values():
            if lyr.source() == self.saveShpNameOriginal:
                QgsMapLayerRegistry.instance().removeMapLayer(lyr.id())

        # remove created files
        if os.path.isfile(self.saveReportNameOriginal):
            os.remove(self.saveReportNameOriginal) 
        
        try:
            if os.path.isfile(self.saveCsvNameOriginal):
                os.remove(self.saveCsvNameOriginal)
        except:
            pass
            
        if os.path.isfile(self.saveShpNameOriginal):
            os.remove(self.saveShpNameOriginal)
        
        if os.path.isfile(self.saveShpNameOriginal.split('.')[0]+'.shx'):
            os.remove(self.saveShpNameOriginal.split('.')[0]+'.shx')
            
        if os.path.isfile(self.saveShpNameOriginal.split('.')[0]+'.dbf'):
            os.remove(self.saveShpNameOriginal.split('.')[0]+'.dbf')
        
        if os.path.isfile(self.saveShpNameOriginal.split('.')[0]+'.prj'):
            os.remove(self.saveShpNameOriginal.split('.')[0]+'.prj')

    def progressBar(self):
        """Initializing progress bar.
            """
        self.progressMessageBar = iface.messageBar().createMessage(u"Ground Radiation Monitoring:",u" Computing...")
        self.progress = QProgressBar()
        self.progress.setMaximum(100)
        self.progress.setAlignment(Qt.AlignLeft|Qt.AlignVCenter)
        
        self.cancelButton = QtGui.QPushButton()
        self.cancelButton.setText('Cancel')
        self.progressMessageBar.layout().addWidget(self.cancelButton)
        self.progressMessageBar.layout().addWidget(self.progress)
        
        msgBar = self.iface.messageBar()
        msgBar.pushWidget(self.progressMessageBar, iface.messageBar().INFO)
        #hide x button
        msgBar.findChildren(QToolButton)[0].setHidden(True)

        self.cancelButton.clicked.connect(self.onCancelButton)

    def setStatus(self, num, text):
        """Update progress status.
        
        :num: progress percent
        """

        self.progress.setFormat(text)
        self.progress.setValue(num)

    def addNewLayer(self):
        """End computeThread.
        
        Ask to add new layer of computed points to map canvas. """

        # Message box    
        reply  = QMessageBox.question(self, u'Ground Radiation Monitoring',
                                            u"Add new layer to map canvas?",
                                            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
                                            QtGui.QMessageBox.Yes)
        # add map layer to map canvas
        if reply == QMessageBox.Yes:
            newLayer = iface.addVectorLayer(u'{f}'.format(f=self.saveShpNameOriginal),
                                             u'{f}'.format(f=QFileInfo(self.saveShpNameOriginal).baseName()), "ogr")  
        
        self.iface.messageBar().popWidget(self.progressMessageBar)
        self.save_button.setEnabled(True)

    def sendMessage(self, caption, message, type):
        if type == 'CRITICAL':
            self.iface.messageBar().pushMessage(self.tr(u'{}').format(caption),
                                                self.tr(u'{}').format(message),
                                                level = QgsMessageBar.CRITICAL, duration = 5)
        elif type == 'INFO':
            self.iface.messageBar().pushMessage(self.tr(u'{}').format(caption),
                                                self.tr(u'{}').format(message),
                                                level = QgsMessageBar.INFO, duration = 5)