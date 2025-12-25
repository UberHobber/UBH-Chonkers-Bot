# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main.ui'
##
## Created by: Qt User Interface Compiler version 6.10.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient,
    QCursor, QFont, QFontDatabase, QGradient,
    QIcon, QImage, QKeySequence, QLinearGradient,
    QPainter, QPalette, QPixmap, QRadialGradient,
    QTransform)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QGridLayout,
    QGroupBox, QLabel, QMainWindow, QMenu,
    QMenuBar, QProgressBar, QPushButton, QSizePolicy,
    QSpacerItem, QStatusBar, QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.setEnabled(True)
        MainWindow.resize(560, 290)
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(MainWindow.sizePolicy().hasHeightForWidth())
        MainWindow.setSizePolicy(sizePolicy)
        MainWindow.setMinimumSize(QSize(560, 290))
        MainWindow.setMaximumSize(QSize(560, 290))
        self.actionSet_Data_Location = QAction(MainWindow)
        self.actionSet_Data_Location.setObjectName(u"actionSet_Data_Location")
        self.actionSet_Secrets_Location = QAction(MainWindow)
        self.actionSet_Secrets_Location.setObjectName(u"actionSet_Secrets_Location")
        self.actionLog_to_file = QAction(MainWindow)
        self.actionLog_to_file.setObjectName(u"actionLog_to_file")
        self.actionLog_to_file.setCheckable(True)
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.gridLayout_4 = QGridLayout(self.centralwidget)
        self.gridLayout_4.setObjectName(u"gridLayout_4")
        self.grid_00 = QGridLayout()
        self.grid_00.setObjectName(u"grid_00")
        self.btn_process = QPushButton(self.centralwidget)
        self.btn_process.setObjectName(u"btn_process")

        self.grid_00.addWidget(self.btn_process, 2, 0, 1, 1)

        self.grid_02_checkboxes = QGridLayout()
        self.grid_02_checkboxes.setObjectName(u"grid_02_checkboxes")
        self.chk_members = QCheckBox(self.centralwidget)
        self.chk_members.setObjectName(u"chk_members")

        self.grid_02_checkboxes.addWidget(self.chk_members, 0, 0, 1, 1)

        self.chk_timeout = QCheckBox(self.centralwidget)
        self.chk_timeout.setObjectName(u"chk_timeout")

        self.grid_02_checkboxes.addWidget(self.chk_timeout, 1, 0, 1, 1)

        self.verticalSpacer_2 = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.grid_02_checkboxes.addItem(self.verticalSpacer_2, 2, 0, 1, 1)


        self.grid_00.addLayout(self.grid_02_checkboxes, 1, 0, 1, 1)

        self.grid_01_talent = QGridLayout()
        self.grid_01_talent.setObjectName(u"grid_01_talent")
        self.cb_talent = QComboBox(self.centralwidget)
        self.cb_talent.setObjectName(u"cb_talent")

        self.grid_01_talent.addWidget(self.cb_talent, 2, 0, 1, 1)

        self.lbl_talent = QLabel(self.centralwidget)
        self.lbl_talent.setObjectName(u"lbl_talent")

        self.grid_01_talent.addWidget(self.lbl_talent, 1, 0, 1, 1)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.grid_01_talent.addItem(self.verticalSpacer, 3, 0, 1, 1)


        self.grid_00.addLayout(self.grid_01_talent, 0, 0, 1, 1)

        self.grid_03_progress = QGridLayout()
        self.grid_03_progress.setObjectName(u"grid_03_progress")
        self.label = QLabel(self.centralwidget)
        self.label.setObjectName(u"label")

        self.grid_03_progress.addWidget(self.label, 0, 0, 1, 1)

        self.prg_bar = QProgressBar(self.centralwidget)
        self.prg_bar.setObjectName(u"prg_bar")
        self.prg_bar.setValue(24)

        self.grid_03_progress.addWidget(self.prg_bar, 1, 0, 1, 1)

        self.verticalSpacer_3 = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.grid_03_progress.addItem(self.verticalSpacer_3, 2, 0, 1, 1)


        self.grid_00.addLayout(self.grid_03_progress, 3, 0, 1, 1)


        self.gridLayout_4.addLayout(self.grid_00, 0, 0, 2, 1)

        self.groupBox = QGroupBox(self.centralwidget)
        self.groupBox.setObjectName(u"groupBox")
        self.gridLayout = QGridLayout(self.groupBox)
        self.gridLayout.setObjectName(u"gridLayout")
        self.label_12 = QLabel(self.groupBox)
        self.label_12.setObjectName(u"label_12")

        self.gridLayout.addWidget(self.label_12, 0, 2, 1, 1)

        self.label_9 = QLabel(self.groupBox)
        self.label_9.setObjectName(u"label_9")

        self.gridLayout.addWidget(self.label_9, 3, 2, 1, 1)

        self.label_18 = QLabel(self.groupBox)
        self.label_18.setObjectName(u"label_18")

        self.gridLayout.addWidget(self.label_18, 6, 0, 1, 1)

        self.label_4 = QLabel(self.groupBox)
        self.label_4.setObjectName(u"label_4")

        self.gridLayout.addWidget(self.label_4, 1, 0, 1, 1)

        self.label_7 = QLabel(self.groupBox)
        self.label_7.setObjectName(u"label_7")

        self.gridLayout.addWidget(self.label_7, 5, 0, 1, 1)

        self.label_5 = QLabel(self.groupBox)
        self.label_5.setObjectName(u"label_5")

        self.gridLayout.addWidget(self.label_5, 3, 0, 1, 1)

        self.label_8 = QLabel(self.groupBox)
        self.label_8.setObjectName(u"label_8")

        self.gridLayout.addWidget(self.label_8, 5, 2, 1, 1)

        self.label_17 = QLabel(self.groupBox)
        self.label_17.setObjectName(u"label_17")

        self.gridLayout.addWidget(self.label_17, 6, 2, 1, 1)

        self.label_6 = QLabel(self.groupBox)
        self.label_6.setObjectName(u"label_6")

        self.gridLayout.addWidget(self.label_6, 4, 0, 1, 1)

        self.label_27 = QLabel(self.groupBox)
        self.label_27.setObjectName(u"label_27")

        self.gridLayout.addWidget(self.label_27, 2, 2, 1, 1)

        self.label_3 = QLabel(self.groupBox)
        self.label_3.setObjectName(u"label_3")

        self.gridLayout.addWidget(self.label_3, 0, 0, 1, 1)

        self.label_28 = QLabel(self.groupBox)
        self.label_28.setObjectName(u"label_28")

        self.gridLayout.addWidget(self.label_28, 2, 0, 1, 1)

        self.label_10 = QLabel(self.groupBox)
        self.label_10.setObjectName(u"label_10")

        self.gridLayout.addWidget(self.label_10, 1, 2, 1, 1)

        self.label_11 = QLabel(self.groupBox)
        self.label_11.setObjectName(u"label_11")

        self.gridLayout.addWidget(self.label_11, 4, 2, 1, 1)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.gridLayout.addItem(self.horizontalSpacer, 2, 1, 1, 1)


        self.gridLayout_4.addWidget(self.groupBox, 0, 1, 2, 1)

        self.groupBox_2 = QGroupBox(self.centralwidget)
        self.groupBox_2.setObjectName(u"groupBox_2")
        self.gridLayout_2 = QGridLayout(self.groupBox_2)
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        self.label_14 = QLabel(self.groupBox_2)
        self.label_14.setObjectName(u"label_14")

        self.gridLayout_2.addWidget(self.label_14, 1, 0, 1, 1)

        self.label_30 = QLabel(self.groupBox_2)
        self.label_30.setObjectName(u"label_30")

        self.gridLayout_2.addWidget(self.label_30, 2, 0, 1, 1)

        self.label_29 = QLabel(self.groupBox_2)
        self.label_29.setObjectName(u"label_29")

        self.gridLayout_2.addWidget(self.label_29, 2, 2, 1, 1)

        self.label_13 = QLabel(self.groupBox_2)
        self.label_13.setObjectName(u"label_13")

        self.gridLayout_2.addWidget(self.label_13, 0, 0, 1, 1)

        self.label_22 = QLabel(self.groupBox_2)
        self.label_22.setObjectName(u"label_22")

        self.gridLayout_2.addWidget(self.label_22, 0, 2, 1, 1)

        self.label_20 = QLabel(self.groupBox_2)
        self.label_20.setObjectName(u"label_20")

        self.gridLayout_2.addWidget(self.label_20, 1, 2, 1, 1)

        self.horizontalSpacer_2 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.gridLayout_2.addItem(self.horizontalSpacer_2, 0, 1, 1, 1)


        self.gridLayout_4.addWidget(self.groupBox_2, 0, 2, 1, 1)

        self.groupBox_3 = QGroupBox(self.centralwidget)
        self.groupBox_3.setObjectName(u"groupBox_3")
        self.gridLayout_3 = QGridLayout(self.groupBox_3)
        self.gridLayout_3.setObjectName(u"gridLayout_3")
        self.label_15 = QLabel(self.groupBox_3)
        self.label_15.setObjectName(u"label_15")

        self.gridLayout_3.addWidget(self.label_15, 0, 0, 1, 1)

        self.label_23 = QLabel(self.groupBox_3)
        self.label_23.setObjectName(u"label_23")

        self.gridLayout_3.addWidget(self.label_23, 0, 2, 1, 1)

        self.label_26 = QLabel(self.groupBox_3)
        self.label_26.setObjectName(u"label_26")

        self.gridLayout_3.addWidget(self.label_26, 2, 0, 1, 1)

        self.label_24 = QLabel(self.groupBox_3)
        self.label_24.setObjectName(u"label_24")

        self.gridLayout_3.addWidget(self.label_24, 3, 2, 1, 1)

        self.label_21 = QLabel(self.groupBox_3)
        self.label_21.setObjectName(u"label_21")

        self.gridLayout_3.addWidget(self.label_21, 1, 2, 1, 1)

        self.label_25 = QLabel(self.groupBox_3)
        self.label_25.setObjectName(u"label_25")

        self.gridLayout_3.addWidget(self.label_25, 2, 2, 1, 1)

        self.label_19 = QLabel(self.groupBox_3)
        self.label_19.setObjectName(u"label_19")

        self.gridLayout_3.addWidget(self.label_19, 3, 0, 1, 1)

        self.label_16 = QLabel(self.groupBox_3)
        self.label_16.setObjectName(u"label_16")

        self.gridLayout_3.addWidget(self.label_16, 1, 0, 1, 1)

        self.horizontalSpacer_3 = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.gridLayout_3.addItem(self.horizontalSpacer_3, 0, 1, 1, 1)


        self.gridLayout_4.addWidget(self.groupBox_3, 1, 2, 1, 1)

        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 560, 22))
        self.menuOptions = QMenu(self.menubar)
        self.menuOptions.setObjectName(u"menuOptions")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.menubar.addAction(self.menuOptions.menuAction())
        self.menuOptions.addAction(self.actionSet_Data_Location)
        self.menuOptions.addAction(self.actionSet_Secrets_Location)
        self.menuOptions.addAction(self.actionLog_to_file)

        self.retranslateUi(MainWindow)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"Youtube Database Updater", None))
        self.actionSet_Data_Location.setText(QCoreApplication.translate("MainWindow", u"Set Data Location", None))
        self.actionSet_Secrets_Location.setText(QCoreApplication.translate("MainWindow", u"Set Secrets Location", None))
        self.actionLog_to_file.setText(QCoreApplication.translate("MainWindow", u"Log to file", None))
        self.btn_process.setText(QCoreApplication.translate("MainWindow", u"Process", None))
        self.chk_members.setText(QCoreApplication.translate("MainWindow", u"Member's Only", None))
        self.chk_timeout.setText(QCoreApplication.translate("MainWindow", u"Timeout Scraping", None))
        self.lbl_talent.setText(QCoreApplication.translate("MainWindow", u"Talent", None))
        self.label.setText(QCoreApplication.translate("MainWindow", u"Video Progress", None))
        self.prg_bar.setFormat(QCoreApplication.translate("MainWindow", u"%v/%m", None))
        self.groupBox.setTitle(QCoreApplication.translate("MainWindow", u"Video Stats", None))
        self.label_12.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_9.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_18.setText(QCoreApplication.translate("MainWindow", u"Total", None))
        self.label_4.setText(QCoreApplication.translate("MainWindow", u"Existing", None))
        self.label_7.setText(QCoreApplication.translate("MainWindow", u"Errors", None))
        self.label_5.setText(QCoreApplication.translate("MainWindow", u"No Chat", None))
        self.label_8.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_17.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_6.setText(QCoreApplication.translate("MainWindow", u"Unavailable", None))
        self.label_27.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_3.setText(QCoreApplication.translate("MainWindow", u"New/Updated", None))
        self.label_28.setText(QCoreApplication.translate("MainWindow", u"Still Live", None))
        self.label_10.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_11.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.groupBox_2.setTitle(QCoreApplication.translate("MainWindow", u"Message Stats", None))
        self.label_14.setText(QCoreApplication.translate("MainWindow", u"Existing", None))
        self.label_30.setText(QCoreApplication.translate("MainWindow", u"Total", None))
        self.label_29.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_13.setText(QCoreApplication.translate("MainWindow", u"New", None))
        self.label_22.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_20.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.groupBox_3.setTitle(QCoreApplication.translate("MainWindow", u"User Stats", None))
        self.label_15.setText(QCoreApplication.translate("MainWindow", u"New", None))
        self.label_23.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_26.setText(QCoreApplication.translate("MainWindow", u"Total", None))
        self.label_24.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_21.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_25.setText(QCoreApplication.translate("MainWindow", u"0", None))
        self.label_19.setText(QCoreApplication.translate("MainWindow", u"Invalid", None))
        self.label_16.setText(QCoreApplication.translate("MainWindow", u"Existing", None))
        self.menuOptions.setTitle(QCoreApplication.translate("MainWindow", u"Options", None))
    # retranslateUi

