import java.util.Properties

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// Firma release con el keystore de Riveros si esta presente (keystore.properties no versionado)
val keystorePropsFile = rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystorePropsFile.exists()) load(keystorePropsFile.inputStream())
}

android {
    namespace = "com.riveros.playme"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.riveros.playme"
        minSdk = 24
        targetSdk = 35
        versionCode = 3
        versionName = "1.2.1"
        // x86_64 solo servia para el emulador: +11 MB en el APK de release
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
    }

    signingConfigs {
        if (keystorePropsFile.exists()) {
            create("release") {
                storeFile = rootProject.file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystorePropsFile.exists()) signingConfig = signingConfigs.getByName("release")
        }
        debug {
            if (keystorePropsFile.exists()) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        jniLibs { useLegacyPackaging = true }
    }

    sourceSets["main"].assets.srcDirs("src/main/assets")
}

chaquopy {
    defaultConfig {
        version = "3.11"
        // pin: una release nueva de yt-dlp no debe cambiar el APK sin tocar el repo
        pip { install("yt-dlp==2026.8.19") }
        // nota: pyc { src = false } no existe en Chaquopy 16.1.0 (el APK lleva
        // los .py en claro; no es un riesgo de seguridad, es tamano)
    }
}
