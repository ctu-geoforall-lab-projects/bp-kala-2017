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
from datetime import datetime

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
    lengthMeasure = QgsDistanceArea()
    lengthMeasure.setEllipsoid('WGS84')
    lengthMeasure.setEllipsoidalMode(True)
    
    # set signals
    computeEnd = pyqtSignal()
    computeStat = pyqtSignal(int,str)
    computeProgress = pyqtSignal()
    computeMessage = pyqtSignal(str,str,str)

    def __init__(self,  rasterLayerId, trackLayerId, reportFileName, csvFileName, shpFileName, userDistanceBetweenVertices, userSpeed, userUnits):
        QThread.__init__(self)
        self.rasterLayerId = rasterLayerId
        self.trackLayerId = trackLayerId
        self.reportFileName = reportFileName
        self.csvFileName = csvFileName
        self.shpFileName = shpFileName
        self.userDistanceBetweenVertices = userDistanceBetweenVertices
        self.userSpeed = userSpeed
        self.userUnits = userUnits

    def run(self):
        """Run compute thread."""
        
        self.computeProgress.emit()
        
        self.abort = False

        rasterLayer = QgsMapLayerRegistry.instance().mapLayer(self.rasterLayerId)
        trackLayer = QgsMapLayerRegistry.instance().mapLayer(self.trackLayerId)
        trackName = QgsMapLayerRegistry.instance().mapLayer(self.trackLayerId).name()

        # get coordinates of vertices based on user defined sample segment length
        verticesX, verticesY = self.getTrackVertices(trackLayer)

        if self.abort == True:
            return

        atributeTableData, statisticsData = self.getStatistics(verticesX, verticesY, rasterLayer)

        if self.abort == True:
            return
        
        self.createReport(trackName, statisticsData)
        
        if self.abort == True:
            return
        
        if self.csvFileName != None:
            self.createCsv(atributeTableData)

        if self.abort == True:
            return
        
        self.createShp(atributeTableData, trackLayer)

        if self.abort == True:
            return
        
        self.computeEnd.emit()
    
    def abortThread(self):
        self.abort = True
      
    def getTrackVertices(self, trackLayer):
        """Get coordinates of vertices of sampled track.

        :trackLayer: linestring vector layer to be sampled (QgsVectorLayer)
        """

        # declare arrays of coordinates of vertices and of distance between them
        verticesX = array('d',[])
        verticesY = array('d',[])

        # get coordinates of vertices of uploaded track layer
        for featureIndex, feature in enumerate(trackLayer.getFeatures()):
            
            if self.abort == True:
                    break


            polyline = feature.geometry().asPolyline()
            currentPointIndex = 0
            verticesX.append(polyline[0][0])
            verticesY.append(polyline[0][1])
            polylinePointsCount = len(polyline)
            for i in range(0, polylinePointsCount-1):
                
                if self.abort == True:
                    break

                self.computeStat.emit(float(i)/polylinePointsCount * 10, u'(1/3) Sampling track...')
                
                point1 = polyline[i]
                point2 = polyline[i+1]
                distance = self.getDistance(point1, point2)
                
                # check whether the input distance between vertices is longer then the distance between points
                if distance > self.userDistanceBetweenVertices and self.userDistanceBetweenVertices != 0:
                    newX, newY = self.sampleLine(point1,point2, distance)
                    verticesX.extend(newX)
                    verticesY.extend(newY)
                else:
                    verticesX.append(point2[0])
                    verticesY.append(point2[1])
 
        # returns coordinates of all vertices of track   
        return verticesX, verticesY

    def sampleLine(self, point1, point2, distanceBetweenPoints):
        """Sample line between two points to segments of user selected length.

        Compute coordinates of new vertices.

        :point1: first point of line
        :point2: last point of line
        :distanceBetweenPoints: length of line between point1 and point2 in metres
        """

        # number of vertices, that should be added between 2 points
        verticesQuantity = ceil(distanceBetweenPoints / float(self.userDistanceBetweenVertices)) - 1

        # if modulo of division of line length and 1 segment length is not 0,
        # point where last complete segment ends is computed
        lastPointX = lastPointY = None
        if distanceBetweenPoints % self.userDistanceBetweenVertices != 0:
            shortestSegmentRelative = (distanceBetweenPoints % self.userDistanceBetweenVertices) / distanceBetweenPoints
            lastPointX = point2[0] - (point2[0] - point1[0]) * shortestSegmentRelative
            lastPointY = point2[1] - (point2[1] - point1[1]) * shortestSegmentRelative
            vectorX = lastPointX - point1[0]
            vectorY = lastPointY - point1[1] 
        else:
            vectorX = point2[0] - point1[0]
            vectorY = point2[1] - point1[1]

        # compute addition to coordinates with size of 1 segment    
        addX = vectorX / verticesQuantity
        addY = vectorY / verticesQuantity

        # declare arrays for newly computed points
        newX = array('d',[])
        newY = array('d',[])

        # compute new points
        for n in range(1,int(verticesQuantity)):
            newX.append((point1[0]+n*addX))
            newY.append((point1[1]+n*addY))
        if lastPointX:
            newX.append(lastPointX)
            newY.append(lastPointY)
        newX.append(point2[0])
        newY.append(point2[1])

        return newX, newY

    def getStatistics(self, verticesX, verticesY, rasterLayer):
        """Compute statistics.
            
        :verticesX: X coordinates of points
        :verticesY: Y coordinates of points
        :rasterLayer: raster layer to get dose rate from
        """

        # get raster value multiplicator
        if str(self.userUnits) == 'nanoSv/h':
            coef = 0.001

        elif str(self.userUnits) == 'nanoGy/h':
            coef = GroundRadiationMonitoringComputation.COEFICIENT * 0.001
            
        elif str(self.userUnits) == 'microGy/h':
            coef = GroundRadiationMonitoringComputation.COEFICIENT
            
        else:
            coef = 1
        
        atributeTableData = []
        
        lengthOfTrack = 0
        accumulatedDose = 0
        maxDoseRate = 0
        avgDoseRate = 0
        rasterValuePrevious = 0
        totalTime = 0
        timeIntervalPrevious = 0
        verticesCount = len(verticesX)-1
        noDataTime = 0
        noDataDistance = 0
        pointsWithRasterValuesCounter = 0
        
        for i in range(0,len(verticesX)):
            
            if self.abort == True:
                break

            self.computeStat.emit(float(i)/verticesCount * 100, u'(2/3) Computing statistics, creating report file...')
            
            if i == verticesCount:
                distance = 0
            else:
                distance = self.getDistance([verticesX[i],verticesY[i]],[verticesX[i+1],verticesY[i+1]])
            
            # time interval in hours decimal between points, totalTime for cumulative time
            timeInterval = (distance/1000)/self.userSpeed 
            totalTime = totalTime + timeIntervalPrevious
          
            # raster value
            v = rasterLayer.dataProvider().identify(QgsPoint(verticesX[i],verticesY[i]),QgsRaster.IdentifyFormatValue).results()
            rasterValue = v.values()[0]
            
            if rasterValue == None or rasterValue <= 0:
                rasterValue = 0
                noDataTime = noDataTime + timeInterval
                noDataDistance = noDataDistance + distance/1000
                
            elif rasterValue != None:
                rasterValue = rasterValue * coef
                avgDoseRate = avgDoseRate + rasterValue
                pointsWithRasterValuesCounter = pointsWithRasterValuesCounter + 1
            
            estimate = timeIntervalPrevious * rasterValuePrevious
               
            # max dose rate
            if rasterValue > maxDoseRate:
                maxDoseRate = rasterValue
                 
            rasterValuePrevious = rasterValue

            # cumulative dose
            accumulatedDose = accumulatedDose + estimate
            
            # total distance
            lengthOfTrack = lengthOfTrack + distance
            
            atributeTableData.append([verticesX[i],
                                      verticesY[i],
                                      rasterValue, 
                                      self.sec2Time(totalTime), 
                                      timeIntervalPrevious * 3600,
                                      accumulatedDose])

            timeIntervalPrevious = timeInterval
                    
        avgDoseRate = avgDoseRate/pointsWithRasterValuesCounter
        
        statisticsData = [lengthOfTrack/1000, totalTime, noDataTime, noDataDistance, maxDoseRate, avgDoseRate, accumulatedDose]
        
        return atributeTableData, statisticsData    

    def getDistance(self, point1, point2):
        """Compute length between 2 QgsPoints.
        
        :point1: 1st point
        :point2: 2nd point
        """
        return GroundRadiationMonitoringComputation.lengthMeasure.measureLine(QgsPoint(point1[0],point1[1]), QgsPoint(point2[0],point2[1]))

    def createCsv(self, atributeTableData):
        """Create csv file.
        
        Print error when csv file cannot be opened for writing.        
        
        :atributeTableData: list of lists containing [X, Y, dose rate, accumulative time, time interval, accumulative dose] 
              of every point

        """
        
        # open csv file
        try:
            # python 3 (NOT TESTED)
            try:
                with open(self.csvFileName, "w",newline='') as f:
                    f.write(u'X,Y,dose_rate_microSvh,accum_time,time_interval_sec,accum_dose_microSv{linesep}'.format(linesep = os.linesep))
                    writer = csv.writer(f)
                    writer.writerows(atributeTableData)
            # python 2
            except:
                with open(self.csvFileName, "wb") as f:
                    f.write(u'X,Y,dose_rate_microSvh,accum_time,time_interval_sec,accum_dose_microSv{linesep}'.format(linesep = os.linesep))
                    writer = csv.writer(f)
                    writer.writerows(atributeTableData)
                        
        except IOError as e:
            self.computeMessage.emit(u'Error', u'Unable open {} for writing. Reason: {}'.format(self.csvFileName, e),'CRITICAL')
            return
        
    def createReport(self, trackName, statisticsData):
        """Create report file.
        
        lengthOfTrack/1000, totalTime, noDataTime, noDataDistance, maxDoseRate, avgDoseRate, accumulatedDose
        
        Prints error when report txt file cannot be opened for writing.

        :trackLayer: name of track layer
        :statisticsData: list of data to be writen to report file, contains lengthOfTrack, totalTime, noDataTime, noDataDistance, 
                         maxDoseRate, avgDoseRate, accumulatedDose
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
	
	message = (
        u'QGIS ground radiation monitoring plugin report{ls}'.format(ls = os.linesep),
        u'report created: {}'.format(datetime.now().strftime('%d.%m.%Y %H:%M')),
        u'{ls}{ls}Route information{ls}'.format(ls = os.linesep),
        u'--------------------------------------{ls}'.format(ls = os.linesep),
        u'route: {trackName}{ls}'.format(trackName = trackName, 
                                                      ls = os.linesep),
        u'monitoring speed (km/h): {speed}{ls}'.format(speed = self.userSpeed, 
                                                                    ls = os.linesep),
        u'total monitoring time: {time}{ls}'.format(time = self.sec2Time(statisticsData[1]),
                                                                 ls = os.linesep),
        u'total distance (km): {distance}{ls}{ls}'.format(distance = round(statisticsData[0],3),
                                                                       ls = os.linesep),
        
        u'No data{ls}'.format(ls = os.linesep),
        u'--------------------------------------{ls}'.format(ls = os.linesep),
        u'time: {time}{ls}'.format(time = self.sec2Time(statisticsData[2]),
                                                ls = os.linesep),
        u'distance (km): {dist}{ls}{ls}'.format(dist = round(statisticsData[3],3),
                                                             ls = os.linesep),
        
        u'Radiation values (estimated){ls}'.format(ls = os.linesep),
        u'--------------------------------------{ls}'.format(ls = os.linesep),
        u'maximum dose rate (microSv/h): {maxDoseRate}{ls}'.format(maxDoseRate = round(statisticsData[4],3),
                                                                            ls = os.linesep),
        u'average dose rate (microSv/h): {avgDoseRate}{ls}'.format(avgDoseRate = round(statisticsData[5],3),
                                                                            ls = os.linesep),
        u'total dose (microSv): {totalDose}'.format(totalDose = round(statisticsData[6],3)),
        
        u'{ls}{ls}Plugin settings'.format(ls = os.linesep),
        u'{ls}--------------------------------------{ls}'.format(ls = os.linesep),
        u'input raster units: {units}{ls}'.format(units = self.userUnits, 
                                                               ls = os.linesep),
        u'distance between track vertices (m): {dist}{ls}{ls}'.format(dist = self.userDistanceBetweenVertices,
                                                                               ls = os.linesep),
	
	u'Explanations:{ls}'.format(ls = os.linesep),
	u'--------------------------------------{ls}'.format(ls = os.linesep),
	u'- monitoring speed is set by user and is constant for whole track{ls}{ls}'.format(ls = os.linesep),
	u'- for the calculation of the dose estimate is set that 1 Gy / h is{ls}'.format(ls = os.linesep),
	u'  equal to 1 Sv / h as it was not possible to include differences{ls}'.format(ls = os.linesep),
	u'  between various measuring devices, sources of radiation etc.{ls}{ls}'.format(ls = os.linesep),
	u'- these results are informative only and cannot be used for{ls}'.format(ls = os.linesep),
	u'  decision-making in crisis management{ls}'.format(ls = os.linesep)
        )

	for line in message:
	    report.write(line)

	report.close()

    def createShp(self, atributeTableData, trackLayer):
        """Create shapefile.
        
        :atributeTableData: list of lists containing [X, Y, dose rate, accumulative time, time interval, accumulative dose] 
              of every point
        :trackLayer: layer of track to get coordinate system from
        """
        
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
        # layer.CreateField(ogr.FieldDefn("X", ogr.OFTReal))
        # layer.CreateField(ogr.FieldDefn("Y", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("rate uSvh", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("accTime", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("interval s", ogr.OFTReal))
        layer.CreateField(ogr.FieldDefn("accDose", ogr.OFTReal))
        
        i = 0
        rowsCount = len(atributeTableData)
        for values in atributeTableData:
            
            if self.abort == True:
                break
            
            # create the feature
            feature = ogr.Feature(layer.GetLayerDefn())
            # Set the attributes using the values from the delimited text file
            # feature.SetField("X", X)
            # feature.SetField("Y", Y)
            feature.SetField("rate uSvh", values[2])
            feature.SetField("accTime", values[3])
            feature.SetField("interval s", values[4])
            feature.SetField("accDose", values[5])
            
            # Create the point geometry
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint(values[0], values[1])

            # Set the feature geometry using the point
            feature.SetGeometry(point)
            # Create the feature in the layer (shapefile)
            layer.CreateFeature(feature)
            # Dereference the feature
            feature = None
            
            i = i + 1
            self.computeStat.emit(float(i)/rowsCount * 100, u'(3/3) Creating shapefile...')
            
        # Save and close the data source
        dataSource = None
        
    def sec2Time(self, time):
        """ Transform time from hours in decimal to hours, minutes and seconds.
        
        :time: time in hours decimal
        """

        hours = int(time)
        minutes = int((time-hours)*60)
        seconds = int(round(((time-hours)*60-minutes)*60))
        if seconds == 60:
            seconds = 0
            minutes = minutes + 1
        if minutes == 60:
            minutes = 0
            hours = hours + 1
        return '%d:%02d:%02d' % (hours, minutes, seconds)
        
        