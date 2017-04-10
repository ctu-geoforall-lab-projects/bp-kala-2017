# -*- coding: utf-8 -*-
import os

from PyQt4.QtCore import QSettings, QTranslator, qVersion, QCoreApplication, Qt, QFileInfo, pyqtSignal, QThread
from PyQt4.QtGui import QComboBox, QAction, QIcon, QToolButton, QFileDialog
from qgis.core import QgsMapLayerRegistry, QgsMapLayer, QGis, QgsPoint, QgsRaster, QgsProject,  QgsProviderRegistry, QgsDistanceArea
from qgis.utils import QgsMessageBar, iface
from qgis.gui import QgsMapLayerComboBox,QgsMapLayerProxyModel
from osgeo import gdal, ogr
from math import ceil
from array import array

from qgis.core import QgsVectorLayer, QgsField, QgsFeature, QgsGeometry, QgsVectorFileWriter
from PyQt4.QtCore import QVariant  

import osgeo.ogr as ogr
import osgeo.osr as osr
import csv
import codecs

class GroundRadiationMonitoringComputation(QThread):
    # set Gy/Sv coeficient
    COEFICIENT = 1
    
    # set length measurement
    length = QgsDistanceArea()
    length.setEllipsoid('WGS84')
    length.setEllipsoidalMode(True)
    
    # set signals
    computeEnd = pyqtSignal()
    computeStat = pyqtSignal(int,str)
    computeProgress = pyqtSignal()
    computeMessage = pyqtSignal(str,str,str)

    def __init__(self,  rasterLayerId, trackLayerId, reportFileName, csvFileName, shpFileName, vertexDist, speed, units):
        QThread.__init__(self)
        self.rasterLayerId = rasterLayerId
        self.trackLayerId = trackLayerId
        self.reportFileName = reportFileName
        self.csvFileName = csvFileName
        self.shpFileName = shpFileName
        self.vertexDist = vertexDist
        self.speed = speed
        self.units = units

    def run(self):
        """Run compute thread."""
        
        self.computeProgress.emit()
        
        self.abort = False

        rasterLayer = QgsMapLayerRegistry.instance().mapLayer(self.rasterLayerId)
        trackLayer = QgsMapLayerRegistry.instance().mapLayer(self.trackLayerId)
        trackName = QgsMapLayerRegistry.instance().mapLayer(self.trackLayerId).name()

        # get coordinates of vertices based on user defined sample segment length
        vertexX, vertexY = self.getCoor(rasterLayer, trackLayer)

        if self.abort == True:
            return

        distance, time, maxDose, avgDose, totalDose = self.exportValues(vertexX, vertexY, rasterLayer, trackLayer)

        if self.abort == True:
            return

        self.createReport(trackName, time, distance, maxDose, avgDose, totalDose)

        if self.abort == True:
            return

        self.computeEnd.emit()
    
    def abortThread(self):
        self.abort = True
      
    def getCoor(self, rasterLayer, trackLayer):
        """Get coordinates of vertices of sampled track.

        :rasterLayer: input raster layer (QgsRasterLayer)
        :trackLayer: linestring vector layer to be sampled (QgsVectorLayer)
        """

        # declare arrays of coordinates of vertices and of distance between them
        vertexX = array('d',[])
        vertexY = array('d',[])

        # get coordinates of vertices of uploaded track layer
        for featureIndex, feature in enumerate(trackLayer.getFeatures()):
            
            if self.abort == True:
                    break


            polyline = feature.geometry().asPolyline()
            pointCounter = 0
            vertexX.append(polyline[0][0])
            vertexY.append(polyline[0][1])
            amount = len(polyline)
            while pointCounter < (amount-1):
                
                if self.abort == True:
                    break

                self.computeStat.emit(float(pointCounter)/amount * 10, u'(1/2) Sampling track...')
                
                point1 = polyline[pointCounter]
                point2 = polyline[pointCounter+1]
                distance = self.distance(point1, point2)
                
                # check whether the input distance between vertices is longer then the distance between points
                if distance > self.vertexDist and self.vertexDist != 0:
                    newX, newY = self.sampleLine(point1,point2, distance)
                    vertexX.extend(newX)
                    vertexY.extend(newY)
                else:
                    vertexX.append(point2[0])
                    vertexY.append(point2[1])
                pointCounter = pointCounter + 1
 
        # returns coordinates of all vertices of track   
        return vertexX, vertexY

    def sampleLine(self,point1, point2, dist):
        """Sample line between two points to segments of user selected length.

        Compute coordinates of new vertices.

        :point1: first point of line
        :point2: last point of line
        :dist: length of line in metres
        """

        # number of vertices, that should be added between 2 points
        vertexQuantity = ceil(dist / float(self.vertexDist)) - 1

        # if modulo of division of line length and 1 segment length is not 0,
        # point where last complete segment ends is computed
        lastPointX = lastPointY = None
        if dist % self.vertexDist != 0:
            shortestSegmentRel = (dist % self.vertexDist) / dist
            lastPointX = point2[0] - (point2[0] - point1[0]) * shortestSegmentRel
            lastPointY = point2[1] - (point2[1] - point1[1]) * shortestSegmentRel
            vectorX = lastPointX - point1[0]
            vectorY = lastPointY - point1[1] 
        else:
            vectorX = point2[0] - point1[0]
            vectorY = point2[1] - point1[1]

        # compute addition to coordinates with size of 1 segment    
        addX = vectorX / vertexQuantity
        addY = vectorY / vertexQuantity

        # declare arrays for newly computed points
        newX = array('d',[])
        newY = array('d',[])

        # compute new points
        for n in range(1,int(vertexQuantity)):
            newX.append((point1[0]+n*addX))
            newY.append((point1[1]+n*addY))
        if lastPointX:
            newX.append(lastPointX)
            newY.append(lastPointY)
        newX.append(point2[0])
        newY.append(point2[1])

        return newX, newY

    def exportValues(self, vertexX, vertexY, rasterLayer, trackLayer):
        """
        :vertexX: X coordinates of points
        :vertexY: Y coordinates of points
        :rasterLayer: raster layer dose rate is exctracted from
        """
        # open csv file
        try:
            csvFile = open(self.csvFileName, 'wb')
        except IOError as e:
            self.computeMessage.emit(u'Error', u'Unable open {} for writing. Reason: {}'.format(self.csvFileName, e),'CRITICAL')
            return
        
        csvFile.write(self.tr(u'X,Y,dose rate,cumulative{linesep}'.format(linesep = os.linesep)))
        
        # COEFICIENT Gy/Sv
        coef = GroundRadiationMonitoringComputation.COEFICIENT
        
        self.speed = float(self.speed.replace(',', '.'))
        
        # set up the shapefile driver
        driver = ogr.GetDriverByName("ESRI Shapefile")
        
        # create the data source
        dataSource = driver.CreateDataSource(self.tr(u'{f}').format(f=self.shpFileName))

        # create the spatial reference
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(trackLayer.crs().authid()[5:]))

        # create the layer
        layer = dataSource.CreateLayer('{}'.format(self.shpFileName.encode('utf8')), srs, ogr.wkbPoint)

        # Add the fields we're interested in
        layer.CreateField(ogr.FieldDefn("X", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("Y", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("dose rate (nSv/h)", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("cumulative (nSv)", ogr.OFTReal))
        
        i = 0
        totalDistance = 0
        cumulDose = 0
        maxDose = 0
        valueYes = 0
        avgDose = 0
        valuePrev = 0
        for X,Y in zip(vertexX,vertexY):

            if self.abort == True:
                csvFile.close()
                break
            
            i = i + 1
            self.computeStat.emit(float(i)/len(vertexX) * 100, u'(2/2) Computing statistics, creating files...')
            
            if i == len(vertexX):
                dist = 0
            else:
                dist = self.distance([vertexX[i-1],vertexY[i-1]],[vertexX[i],vertexY[i]])

            # time interval between points
            interval = (dist/1000)/float(self.speed)
                        
            # raster value
            v = rasterLayer.dataProvider().identify(QgsPoint(X,Y),QgsRaster.IdentifyFormatValue).results()
            value = v.values()[0]
            
            if value != None:
                if str(self.units) == 'microSv/h':
                    value = value * 1000
            
                elif str(self.units) == 'nanoGy/h':
                    value = value * coef
                
                elif str(self.units) == 'microGy/h':
                    value = value * coef * 1000
                
                # dose on interval
                if valuePrev == None:
                    estimate = 0
                else:
                    # dose on interval
                    estimate = interval * valuePrev
                
                # counter of points with raster value
                valueYes = valueYes + 1
                
                # add dose rate on point to compute avg dose rate later
                avgDose = avgDose + value
                
                # max dose rate
                if value > maxDose:
                    maxDose = value
            
            elif valuePrev != None:
                estimate = interval * valuePrev
                
            else:
                estimate = 0  
                
            valuePrev = value

            # cumulative dose
            cumulDose = cumulDose + estimate
            
            # total distance
            totalDistance = totalDistance + dist
            
            # write to csv file
            csvFile.write(self.tr(u'{valX},{valY},{val},{cumul}{linesep}'.format(valX = X,
                                                                                 valY = Y,
                                                                                 val = value, 
                                                                                 cumul = cumulDose,
                                                                                 linesep=os.linesep)))
            
            # write to shape file
             # create the feature
            feature = ogr.Feature(layer.GetLayerDefn())
            # Set the attributes using the values from the delimited text file
            feature.SetField("X", X)
            feature.SetField("Y", Y)
            feature.SetField("dose rate (nSv/h)", value)
            feature.SetField("cumulative (nSv)", cumulDose)

            # Create the point geometry
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint(X, Y)

            # Set the feature geometry using the point
            feature.SetGeometry(point)
            # Create the feature in the layer (shapefile)
            layer.CreateFeature(feature)
            # Dereference the feature
            feature = None
            
        # Save and close the data source
        dataSource = None
        
        # close output csv file
        csvFile.close() 
           
        # avg dose {here cumulDose = totalDose)
        if valueYes == 0:
            avgDose = None
        else:
            avgDose = avgDose/valueYes
        
        # total time
        decTime = totalDistance/(float(self.speed) * 1000)
        hours = int(decTime)
        minutes = int((decTime-hours)*60)
        seconds = int(round(((decTime-hours)*60-minutes)*60))
        time = [hours, minutes, seconds]
        
        # close output file
        csvFile.close()        
        
        return totalDistance/1000, time, maxDose, avgDose, cumulDose

    def distance(self, point1, point2):
        """Compute length between 2 QgsPoints.
        
        :point1: 1st point
        :point2: 2nd point
        """
        distance = GroundRadiationMonitoringComputation.length.measureLine(QgsPoint(point1[0],point1[1]), QgsPoint(point2[0],point2[1]))
        return distance


    def createReport(self, trackName, time, distance, maxDose, avgDose, totalDose):
        """Create report file.
        
        Prints error when report txt file cannot be opened for writing.

        :trackLayer: name of track layer
        :time: monitoring time
        :distance: length of route
        :maxDose: maximal dose rate on route
        :avgDose: average dose rate on route
        :totalDose: total dose rate on route
        """
        try:
            try:
                # python 3.x
                report = open(self.reportFileName, 'w', encoding='utf-8')
            except:
                # python 2.x
                report = codecs.open(self.reportFileName, 'w', encoding='utf-8')
        except IOError as e:
            self.computeMessage.emit(u'Error', u'Unable open {} for writing. Reason: {}'.format(self.reportFileName, e),'CRITICAL')
            return

        report.write(u'QGIS ground radiation monitoring plugin report{ls}{ls}'.format(ls = os.linesep))
        report.write(u'Route information{ls}'.format(ls = os.linesep))
        report.write(u'--------------------------------------{ls}'.format(ls = os.linesep))
        report.write(u'route: {trackName}{ls}'.format(trackName = trackName, 
                                                      ls = os.linesep))
        report.write(u'monitoring speed (km/h): {speed}{ls}'.format(speed = self.speed, 
                                                                    ls = os.linesep))
        report.write(u'total monitoring time: {hours}:{minutes}:{seconds}{ls}'.format(hours = time[0],
                                                                                  minutes = time[1],
                                                                                  seconds = time[2],
                                                                                  ls = os.linesep))
        report.write(u'total distance (km): {distance}{ls}{ls}'.format(distance = distance,
                                                                   ls = os.linesep))
        report.write(u'Radiation values (estimated){ls}'.format(ls = os.linesep))
        report.write(u'--------------------------------------{ls}'.format(ls = os.linesep))
        report.write(u'maximum dose rate (nSv/h): {maxDose}{ls}'.format(maxDose = maxDose,
                                                                        ls = os.linesep))
        report.write(u'average dose rate (nSv/h): {avgDose}{ls}'.format(avgDose = avgDose,
                                                                        ls = os.linesep))
        report.write(u'total dose (nSv): {totalDose}'.format(totalDose = totalDose))

        report.close()
